package main

import (
	"bufio"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"hash/fnv"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	jetstream "github.com/bluesky-social/jetstream"
)

const (
	stateVersion = 3
	collection   = "app.bsky.feed.post"
)

var (
	urlRE        = regexp.MustCompile(`https?://[^\s<>"'\]\[(){}]+`)
	trackingKeys = map[string]bool{
		"ref": true, "ref_": true, "referrer": true,
		"source": true, "src": true, "campaign": true,
		"mc_cid": true, "mc_eid": true, "fbclid": true,
		"gclid": true, "dclid": true, "msclkid": true,
		"igshid": true, "si": true,
	}
)

type Config struct {
	Host           string
	Days           int
	Hours          int
	EndDaysAgo     int
	MaxGapHours    float64
	MaxSimilarity  float64
	Top            int
	Buckets        int
	OutDir         string
	APIKey         string
	IncludeReplies bool
	AnalyzeOnly    bool
	Reset          bool
	ProgressEvery  int
	ProfileLookups bool
}

type CrawlState struct {
	Version       int       `json:"version"`
	Host          string    `json:"host"`
	Days          int       `json:"days"`
	Hours         int       `json:"hours"`
	WindowHours   int       `json:"window_hours"`
	EndDaysAgo    int       `json:"end_days_ago"`
	WindowStart   time.Time `json:"window_start"`
	WindowEnd     time.Time `json:"window_end"`
	StartAfterSeq uint64    `json:"start_after_seq"`
	EndSeq        uint64    `json:"end_seq"`
	LastCursor    uint64    `json:"last_cursor"`
	Completed     bool      `json:"completed"`
	StartedAt     time.Time `json:"started_at"`
	CompletedAt   time.Time `json:"completed_at,omitempty"`
}

type Segment struct {
	Name           string `json:"name"`
	Index          int64  `json:"index"`
	SizeBytes      int64  `json:"sizeBytes"`
	EventCount     int64  `json:"eventCount"`
	MinSeq         uint64 `json:"minSeq"`
	MaxSeq         uint64 `json:"maxSeq"`
	MinWitnessedAt int64  `json:"minWitnessedAt"`
	MaxWitnessedAt int64  `json:"maxWitnessedAt"`
}

type SegmentResponse struct {
	Cursor   string    `json:"cursor"`
	Segments []Segment `json:"segments"`
}

type LinkRow struct {
	DID       string `json:"did"`
	RKey      string `json:"rkey"`
	CreatedAt int64  `json:"created_at_us"`
	EventAt   int64  `json:"event_at_us"`
	Text      string `json:"text"`
	URL       string `json:"url"`
}

type Pair struct {
	DID        string
	URL        string
	A          LinkRow
	B          LinkRow
	GapHours   float64
	Similarity float64
}

type AuthorStats struct {
	DID                      string
	RepeatedURLs             int
	EligiblePairs            int
	PostsInRepeatedURLGroups int
	Gaps                     []float64
	LatestPairAt             time.Time
	Example                  *Pair
}

type Profile struct {
	DID         string `json:"did"`
	Handle      string `json:"handle"`
	DisplayName string `json:"displayName"`
	Followers   int64  `json:"followersCount"`
	PostsCount  int64  `json:"postsCount"`
}

type ProfilesResponse struct {
	Profiles []Profile `json:"profiles"`
}

func main() {
	cfg := parseFlags()
	if err := run(cfg); err != nil {
		fmt.Fprintln(os.Stderr, "ERROR:", err)
		os.Exit(1)
	}
}

func parseFlags() Config {
	var cfg Config
	flag.StringVar(&cfg.Host, "host", "jetstream.us-west.bsky.network", "Bluesky Jetstream host")
	flag.IntVar(&cfg.Days, "days", 60, "width of historical window in days (ignored when --hours > 0)")
	flag.IntVar(&cfg.Hours, "hours", 0, "width of historical window in hours; overrides --days when > 0")
	flag.IntVar(&cfg.EndDaysAgo, "end-days-ago", 0, "end the historical window N days before now; e.g. --hours 3 --end-days-ago 7")
	flag.Float64Var(&cfg.MaxGapHours, "max-gap-hours", 168, "maximum time between repeated-link posts")
	flag.Float64Var(&cfg.MaxSimilarity, "max-similarity", 0.90, "reject near-duplicate text above this token-Jaccard similarity")
	flag.IntVar(&cfg.Top, "top", 500, "number of accounts to write to ranked_accounts.csv")
	flag.IntVar(&cfg.Buckets, "buckets", 64, "on-disk hash partitions")
	flag.StringVar(&cfg.OutDir, "out", "bsky-repeat-discovery", "output directory")
	flag.StringVar(&cfg.APIKey, "api-key", os.Getenv("JETSTREAM_API_KEY"), "Jetstream archive API key (or set JETSTREAM_API_KEY)")
	flag.BoolVar(&cfg.IncludeReplies, "include-replies", false, "include reply posts (default: skip)")
	flag.BoolVar(&cfg.AnalyzeOnly, "analyze-only", false, "skip crawl and analyze existing bucket files")
	flag.BoolVar(&cfg.Reset, "reset", false, "delete the output directory before starting")
	flag.IntVar(&cfg.ProgressEvery, "progress-every", 100000, "print progress every N post events")
	flag.BoolVar(&cfg.ProfileLookups, "resolve-profiles", true, "resolve top DIDs to current Bluesky handles")
	flag.Parse()
	return cfg
}

func run(cfg Config) error {
	if cfg.Days <= 0 || cfg.Hours < 0 || cfg.Buckets <= 0 || cfg.Top <= 0 || cfg.EndDaysAgo < 0 {
		return errors.New("--days, --buckets and --top must be positive; --hours and --end-days-ago must be >= 0")
	}
	if cfg.MaxSimilarity < 0 || cfg.MaxSimilarity > 1 {
		return errors.New("--max-similarity must be in [0,1]")
	}
	if cfg.Reset && cfg.AnalyzeOnly {
		return errors.New("--reset and --analyze-only cannot be combined")
	}

	out := filepath.Clean(cfg.OutDir)
	if cfg.Reset {
		if err := os.RemoveAll(out); err != nil {
			return err
		}
	}
	if err := os.MkdirAll(filepath.Join(out, "buckets"), 0o755); err != nil {
		return err
	}

	statePath := filepath.Join(out, "state.json")
	var st CrawlState

	if !cfg.AnalyzeOnly {
		var err error
		st, err = loadOrCreateState(cfg, statePath)
		if err != nil {
			return err
		}
		if !st.Completed {
			if err := crawl(cfg, &st, statePath); err != nil {
				return err
			}
		} else {
			fmt.Println("Historical snapshot already completed; reusing existing bucket files.")
		}
	} else {
		var err error
		st, err = loadState(statePath)
		if err != nil {
			return fmt.Errorf("analyze-only requires existing state.json: %w", err)
		}
	}

	fmt.Println("Analyzing repeated-link groups...")
	stats, pairs, err := analyze(cfg, st)
	if err != nil {
		return err
	}
	return writeOutputs(cfg, st, stats, pairs)
}

func loadOrCreateState(cfg Config, path string) (CrawlState, error) {
	if _, err := os.Stat(path); err == nil {
		st, err := loadState(path)
		if err != nil {
			return CrawlState{}, err
		}
		if st.Version != stateVersion {
			return CrawlState{}, fmt.Errorf("state version %d != expected %d; use a new --out directory or --reset", st.Version, stateVersion)
		}
		windowHours := cfg.Days * 24
		if cfg.Hours > 0 {
			windowHours = cfg.Hours
		}
		if st.Host != cfg.Host || st.WindowHours != windowHours || st.EndDaysAgo != cfg.EndDaysAgo {
			return CrawlState{}, fmt.Errorf("existing state uses host=%q window-hours=%d end-days-ago=%d; current host=%q window-hours=%d end-days-ago=%d; use matching flags, a new --out, or --reset",
				st.Host, st.WindowHours, st.EndDaysAgo, cfg.Host, windowHours, cfg.EndDaysAgo)
		}
		fmt.Printf("Resuming fixed snapshot: %s to %s, seq (%d, %d], last cursor %d\n",
			st.WindowStart.Format(time.RFC3339), st.WindowEnd.Format(time.RFC3339),
			st.StartAfterSeq, st.EndSeq, st.LastCursor)
		return st, nil
	} else if !os.IsNotExist(err) {
		return CrawlState{}, err
	}

	now := time.Now().UTC()
	windowHours := cfg.Days * 24
	if cfg.Hours > 0 {
		windowHours = cfg.Hours
	}
	windowEnd := now.Add(-time.Duration(cfg.EndDaysAgo) * 24 * time.Hour)
	windowStart := windowEnd.Add(-time.Duration(windowHours) * time.Hour)

	fmt.Printf("Finding Jetstream sequence range for historical window %s to %s...\n",
		windowStart.Format(time.RFC3339), windowEnd.Format(time.RFC3339))

	startAfter, endSeq, err := findSeqRange(cfg, windowStart, windowEnd)
	if err != nil {
		return CrawlState{}, err
	}
	st := CrawlState{
		Version:       stateVersion,
		Host:          cfg.Host,
		Days:          cfg.Days,
		Hours:         cfg.Hours,
		WindowHours:   windowHours,
		EndDaysAgo:    cfg.EndDaysAgo,
		WindowStart:   windowStart,
		WindowEnd:     windowEnd,
		StartAfterSeq: startAfter,
		EndSeq:        endSeq,
		LastCursor:    startAfter,
		StartedAt:     time.Now().UTC(),
	}
	if err := saveState(path, st); err != nil {
		return CrawlState{}, err
	}
	fmt.Printf("Frozen snapshot: %s to %s, seq (%d, %d]\n",
		windowStart.Format(time.RFC3339), windowEnd.Format(time.RFC3339),
		startAfter, endSeq)
	return st, nil
}

func loadState(path string) (CrawlState, error) {
	var st CrawlState
	b, err := os.ReadFile(path)
	if err != nil {
		return st, err
	}
	err = json.Unmarshal(b, &st)
	return st, err
}

func saveState(path string, st CrawlState) error {
	tmp := path + ".tmp"
	b, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func findSeqRange(cfg Config, start, end time.Time) (uint64, uint64, error) {
	base := strings.TrimRight(cfg.Host, "/")
	if !strings.Contains(base, "://") {
		base = "https://" + base
	}
	client := &http.Client{Timeout: 60 * time.Second}
	var cursor string
	var first *Segment
	var last *Segment
	startUS, endUS := start.UnixMicro(), end.UnixMicro()

	for {
		u, _ := url.Parse(base + "/xrpc/network.bsky.jetstream.listSegments")
		q := u.Query()
		q.Set("limit", "1000")
		if cursor != "" {
			q.Set("cursor", cursor)
		}
		u.RawQuery = q.Encode()

		req, _ := http.NewRequest(http.MethodGet, u.String(), nil)
		if cfg.APIKey != "" {
			req.Header.Set("Authorization", "Bearer "+cfg.APIKey)
		}
		resp, err := client.Do(req)
		if err != nil {
			return 0, 0, err
		}
		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			return 0, 0, err
		}
		if resp.StatusCode/100 != 2 {
			return 0, 0, fmt.Errorf("listSegments: HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
		}

		var page SegmentResponse
		if err := json.Unmarshal(body, &page); err != nil {
			return 0, 0, err
		}
		for i := range page.Segments {
			s := page.Segments[i]
			// Segment overlaps the desired witnessed-at time window.
			if s.MaxWitnessedAt < startUS || s.MinWitnessedAt > endUS {
				continue
			}
			if first == nil {
				cp := s
				first = &cp
			}
			cp := s
			last = &cp
		}
		if page.Cursor == "" {
			break
		}
		cursor = page.Cursor
	}

	if first == nil || last == nil {
		return 0, 0, fmt.Errorf("no sealed Jetstream segments overlap %s to %s",
			start.Format(time.RFC3339), end.Format(time.RFC3339))
	}
	var startAfter uint64
	if first.MinSeq > 0 {
		startAfter = first.MinSeq - 1
	}
	return startAfter, last.MaxSeq, nil
}

type bucketSink struct {
	files   []*os.File
	writers []*bufio.Writer
	enc     []*json.Encoder
}

func openBucketSink(dir string, n int) (*bucketSink, error) {
	s := &bucketSink{}
	for i := 0; i < n; i++ {
		path := filepath.Join(dir, fmt.Sprintf("bucket-%03d.jsonl", i))
		f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
		if err != nil {
			s.Close()
			return nil, err
		}
		w := bufio.NewWriterSize(f, 1<<20)
		s.files = append(s.files, f)
		s.writers = append(s.writers, w)
		s.enc = append(s.enc, json.NewEncoder(w))
	}
	return s, nil
}

func (s *bucketSink) Write(row LinkRow) error {
	h := fnv.New64a()
	_, _ = io.WriteString(h, row.DID)
	_, _ = io.WriteString(h, "\x00")
	_, _ = io.WriteString(h, row.URL)
	idx := int(h.Sum64() % uint64(len(s.enc)))
	return s.enc[idx].Encode(row)
}

func (s *bucketSink) Flush() error {
	for _, w := range s.writers {
		if err := w.Flush(); err != nil {
			return err
		}
	}
	for _, f := range s.files {
		if err := f.Sync(); err != nil {
			return err
		}
	}
	return nil
}

func (s *bucketSink) Close() error {
	var first error
	for _, w := range s.writers {
		if err := w.Flush(); err != nil && first == nil {
			first = err
		}
	}
	for _, f := range s.files {
		if err := f.Close(); err != nil && first == nil {
			first = err
		}
	}
	return first
}

func crawl(cfg Config, st *CrawlState, statePath string) error {
	sink, err := openBucketSink(filepath.Join(cfg.OutDir, "buckets"), cfg.Buckets)
	if err != nil {
		return err
	}
	defer sink.Close()

	opts := []jetstream.Option{
		jetstream.WithKinds([]jetstream.Kind{jetstream.KindCommit}),
		jetstream.WithCollection(collection),
		jetstream.WithAfterSeq(st.LastCursor),
		jetstream.WithBeforeSeq(st.EndSeq),
		jetstream.WithSnapshotOnly(),
		jetstream.WithBatchSize(256),
		jetstream.WithDownloadConcurrency(16),
	}
	if cfg.APIKey != "" {
		opts = append(opts, jetstream.WithAPIKey(cfg.APIKey))
	}
	client, err := jetstream.Subscribe(cfg.Host, opts...)
	if err != nil {
		return err
	}
	defer client.Close()

	ctx := context.Background()
	var postEvents, inWindow, linkPosts, linkRows int64
	lastCheckpoint := st.LastCursor
	lastProgress := int64(0)
	startUS, endUS := st.WindowStart.UnixMicro(), st.WindowEnd.UnixMicro()

	for batch, batchErr := range client.Events(ctx) {
		if batchErr != nil {
			fmt.Fprintln(os.Stderr, "Jetstream recoverable error:", batchErr)
			continue
		}
		if batch == nil {
			continue
		}

		for _, ev := range batch.Events() {
			if ev.Seq > st.EndSeq {
				continue
			}
			postEvents++
			if ev.TimeUS < startUS || ev.TimeUS > endUS {
				continue
			}
			inWindow++

			if ev.Commit == nil || ev.Commit.Collection != collection {
				continue
			}
			if ev.Commit.Operation != jetstream.OpCreate && ev.Commit.Operation != jetstream.OpUpdate {
				continue
			}
			rec := ev.Commit.Record
			if rec == nil {
				continue
			}
			if !cfg.IncludeReplies {
				if v, ok := rec["reply"]; ok && v != nil {
					continue
				}
			}

			text, _ := rec["text"].(string)
			createdUS := ev.TimeUS
			if raw, ok := rec["createdAt"].(string); ok {
				if t, err := time.Parse(time.RFC3339Nano, raw); err == nil {
					createdUS = t.UnixMicro()
				}
			}

			urls := extractURLs(rec, text)
			if len(urls) == 0 {
				continue
			}
			linkPosts++

			seen := make(map[string]struct{}, len(urls))
			for _, rawURL := range urls {
				canon, ok := canonicalizeURL(rawURL)
				if !ok {
					continue
				}
				if _, dup := seen[canon]; dup {
					continue
				}
				seen[canon] = struct{}{}
				row := LinkRow{
					DID:       ev.DID,
					RKey:      ev.Commit.Rkey,
					CreatedAt: createdUS,
					EventAt:   ev.TimeUS,
					Text:      text,
					URL:       canon,
				}
				if err := sink.Write(row); err != nil {
					return err
				}
				linkRows++
			}
		}

		if c := batch.LastCursor(); c > lastCheckpoint {
			lastCheckpoint = c
		}

		if postEvents-lastProgress >= int64(cfg.ProgressEvery) {
			if err := sink.Flush(); err != nil {
				return err
			}
			st.LastCursor = lastCheckpoint
			if err := saveState(statePath, *st); err != nil {
				return err
			}
			stats := client.Stats()
			fmt.Printf("events=%d in_window=%d link_posts=%d link_rows=%d cursor=%d progress=%d/%d residual=%d\n",
				postEvents, inWindow, linkPosts, linkRows, st.LastCursor,
				stats.PlannedThrough, stats.SealedTip, stats.ResidualGap)
			lastProgress = postEvents
		}
	}

	if err := sink.Flush(); err != nil {
		return err
	}
	st.LastCursor = st.EndSeq
	st.Completed = true
	st.CompletedAt = time.Now().UTC()
	if err := saveState(statePath, *st); err != nil {
		return err
	}
	fmt.Printf("Crawl complete: post_events=%d in_window=%d link_posts=%d link_rows=%d\n",
		postEvents, inWindow, linkPosts, linkRows)
	return nil
}

func extractURLs(record map[string]any, text string) []string {
	var out []string
	var walk func(any)
	walk = func(v any) {
		switch x := v.(type) {
		case map[string]any:
			for k, val := range x {
				if k == "uri" || k == "url" {
					if s, ok := val.(string); ok && (strings.HasPrefix(s, "http://") || strings.HasPrefix(s, "https://")) {
						out = append(out, s)
					}
				}
				walk(val)
			}
		case []any:
			for _, item := range x {
				walk(item)
			}
		}
	}
	walk(record)
	for _, m := range urlRE.FindAllString(text, -1) {
		out = append(out, strings.TrimRight(m, ".,;:!?"))
	}
	return out
}

func canonicalizeURL(raw string) (string, bool) {
	raw = strings.TrimSpace(raw)
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return "", false
	}
	host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
	if strings.HasPrefix(host, "www.") {
		host = strings.TrimPrefix(host, "www.")
	}
	port := u.Port()
	if port != "" && port != "80" && port != "443" {
		host += ":" + port
	}

	path := u.EscapedPath()
	if path == "" {
		path = "/"
	}
	if path != "/" {
		path = strings.TrimSuffix(path, "/")
	}

	q := u.Query()
	for k := range q {
		lk := strings.ToLower(k)
		if strings.HasPrefix(lk, "utm_") || trackingKeys[lk] {
			q.Del(k)
		}
	}
	canon := host + path
	if encoded := q.Encode(); encoded != "" {
		canon += "?" + encoded
	}
	return canon, true
}

func analyze(cfg Config, st CrawlState) (map[string]*AuthorStats, []Pair, error) {
	authors := make(map[string]*AuthorStats)
	var allPairs []Pair

	for b := 0; b < cfg.Buckets; b++ {
		path := filepath.Join(cfg.OutDir, "buckets", fmt.Sprintf("bucket-%03d.jsonl", b))
		f, err := os.Open(path)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return nil, nil, err
		}

		type groupKey struct {
			DID string
			URL string
		}
		groups := make(map[groupKey]map[string]LinkRow)

		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 64*1024), 2*1024*1024)
		for sc.Scan() {
			var row LinkRow
			if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
				f.Close()
				return nil, nil, fmt.Errorf("%s: %w", path, err)
			}
			key := groupKey{DID: row.DID, URL: row.URL}
			if groups[key] == nil {
				groups[key] = make(map[string]LinkRow)
			}
			groups[key][row.RKey] = row
		}
		if err := sc.Err(); err != nil {
			f.Close()
			return nil, nil, err
		}
		f.Close()

		for key, uniq := range groups {
			if len(uniq) < 2 {
				continue
			}
			posts := make([]LinkRow, 0, len(uniq))
			for _, row := range uniq {
				posts = append(posts, row)
			}
			sort.Slice(posts, func(i, j int) bool { return posts[i].CreatedAt < posts[j].CreatedAt })

			var groupPairs []Pair
			for i := 1; i < len(posts); i++ {
				for j := i - 1; j >= 0; j-- {
					gap := float64(posts[i].CreatedAt-posts[j].CreatedAt) / float64(time.Hour/time.Microsecond)
					if gap > cfg.MaxGapHours {
						break
					}
					if gap < 0 {
						continue
					}
					sim := textSimilarity(posts[j].Text, posts[i].Text)
					if sim > cfg.MaxSimilarity {
						continue
					}
					p := Pair{
						DID:        key.DID,
						URL:        key.URL,
						A:          posts[j],
						B:          posts[i],
						GapHours:   gap,
						Similarity: sim,
					}
					groupPairs = append(groupPairs, p)
					break
				}
			}
			if len(groupPairs) == 0 {
				continue
			}

			as := authors[key.DID]
			if as == nil {
				as = &AuthorStats{DID: key.DID}
				authors[key.DID] = as
			}
			as.RepeatedURLs++
			as.PostsInRepeatedURLGroups += len(posts)
			for i := range groupPairs {
				p := groupPairs[i]
				as.EligiblePairs++
				as.Gaps = append(as.Gaps, p.GapHours)
				when := time.UnixMicro(p.B.CreatedAt).UTC()
				if when.After(as.LatestPairAt) {
					as.LatestPairAt = when
					cp := p
					as.Example = &cp
				}
				allPairs = append(allPairs, p)
			}
		}
		fmt.Printf("analyzed bucket %d/%d; repeated-link authors so far=%d; candidate pairs=%d\n",
			b+1, cfg.Buckets, len(authors), len(allPairs))
	}

	return authors, allPairs, nil
}

func textSimilarity(a, b string) float64 {
	aa := tokenSet(normalizeText(a))
	bb := tokenSet(normalizeText(b))
	if len(aa) == 0 && len(bb) == 0 {
		return 1
	}
	inter := 0
	for t := range aa {
		if _, ok := bb[t]; ok {
			inter++
		}
	}
	union := len(aa) + len(bb) - inter
	if union == 0 {
		return 1
	}
	return float64(inter) / float64(union)
}

func normalizeText(s string) string {
	s = urlRE.ReplaceAllString(s, " ")
	s = strings.ToLower(s)
	var b strings.Builder
	space := false
	for _, r := range s {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			space = false
		} else if !space {
			b.WriteByte(' ')
			space = true
		}
	}
	return strings.TrimSpace(b.String())
}

func tokenSet(s string) map[string]struct{} {
	m := make(map[string]struct{})
	for _, tok := range strings.Fields(s) {
		m[tok] = struct{}{}
	}
	return m
}

func writeOutputs(cfg Config, st CrawlState, stats map[string]*AuthorStats, pairs []Pair) error {
	ranked := make([]*AuthorStats, 0, len(stats))
	for _, s := range stats {
		ranked = append(ranked, s)
	}
	sort.Slice(ranked, func(i, j int) bool {
		if ranked[i].EligiblePairs != ranked[j].EligiblePairs {
			return ranked[i].EligiblePairs > ranked[j].EligiblePairs
		}
		if ranked[i].RepeatedURLs != ranked[j].RepeatedURLs {
			return ranked[i].RepeatedURLs > ranked[j].RepeatedURLs
		}
		return ranked[i].LatestPairAt.After(ranked[j].LatestPairAt)
	})
	if len(ranked) > cfg.Top {
		ranked = ranked[:cfg.Top]
	}

	profiles := map[string]Profile{}
	if cfg.ProfileLookups && len(ranked) > 0 {
		dids := make([]string, 0, len(ranked))
		for _, s := range ranked {
			dids = append(dids, s.DID)
		}
		fmt.Printf("Resolving %d current Bluesky profiles...\n", len(dids))
		profiles = resolveProfiles(dids)
	}

	accountsPath := filepath.Join(cfg.OutDir, "ranked_accounts.csv")
	af, err := os.Create(accountsPath)
	if err != nil {
		return err
	}
	aw := csv.NewWriter(af)
	_ = aw.Write([]string{
		"rank", "handle", "display_name", "did", "followers",
		"repeated_urls", "eligible_pairs", "pairs_per_week",
		"posts_in_repeated_url_groups", "median_gap_hours",
		"latest_pair_at", "example_url", "example_text_a", "example_text_b",
	})
	for i, s := range ranked {
		p := profiles[s.DID]
		exURL, exA, exB := "", "", ""
		if s.Example != nil {
			exURL, exA, exB = s.Example.URL, s.Example.A.Text, s.Example.B.Text
		}
		_ = aw.Write([]string{
			strconv.Itoa(i + 1),
			p.Handle,
			p.DisplayName,
			s.DID,
			strconv.FormatInt(p.Followers, 10),
			strconv.Itoa(s.RepeatedURLs),
			strconv.Itoa(s.EligiblePairs),
			fmt.Sprintf("%.3f", float64(s.EligiblePairs)*168/float64(st.WindowHours)),
			strconv.Itoa(s.PostsInRepeatedURLGroups),
			fmt.Sprintf("%.2f", median(s.Gaps)),
			s.LatestPairAt.Format(time.RFC3339),
			exURL, exA, exB,
		})
	}
	aw.Flush()
	if err := aw.Error(); err != nil {
		af.Close()
		return err
	}
	af.Close()

	pairsPath := filepath.Join(cfg.OutDir, "candidate_pairs.csv")
	pf, err := os.Create(pairsPath)
	if err != nil {
		return err
	}
	pw := csv.NewWriter(pf)
	_ = pw.Write([]string{
		"did", "handle", "canonical_url",
		"a_rkey", "a_created_at", "a_text",
		"b_rkey", "b_created_at", "b_text",
		"gap_hours", "text_jaccard",
	})
	for _, p := range pairs {
		prof := profiles[p.DID]
		_ = pw.Write([]string{
			p.DID, prof.Handle, p.URL,
			p.A.RKey, time.UnixMicro(p.A.CreatedAt).UTC().Format(time.RFC3339), p.A.Text,
			p.B.RKey, time.UnixMicro(p.B.CreatedAt).UTC().Format(time.RFC3339), p.B.Text,
			fmt.Sprintf("%.2f", p.GapHours),
			fmt.Sprintf("%.4f", p.Similarity),
		})
	}
	pw.Flush()
	if err := pw.Error(); err != nil {
		pf.Close()
		return err
	}
	pf.Close()

	summary := map[string]any{
		"window_start":               st.WindowStart,
		"window_end":                 st.WindowEnd,
		"window_days":                float64(st.WindowHours) / 24.0,
		"window_hours":               st.WindowHours,
		"end_days_ago":               st.EndDaysAgo,
		"snapshot_start_after_seq":   st.StartAfterSeq,
		"snapshot_end_seq":           st.EndSeq,
		"authors_with_eligible_pair": len(stats),
		"eligible_pairs":             len(pairs),
		"ranked_accounts_written":    len(ranked),
		"max_gap_hours":              cfg.MaxGapHours,
		"max_text_jaccard":           cfg.MaxSimilarity,
	}
	sb, _ := json.MarshalIndent(summary, "", "  ")
	if err := os.WriteFile(filepath.Join(cfg.OutDir, "summary.json"), sb, 0o644); err != nil {
		return err
	}

	fmt.Println()
	fmt.Printf("DONE. Historical window: %s to %s\n",
		st.WindowStart.Format(time.RFC3339), st.WindowEnd.Format(time.RFC3339))
	fmt.Printf("Authors with >=1 eligible repeated-link pair: %d\n", len(stats))
	fmt.Printf("Eligible historical pairs: %d\n", len(pairs))
	fmt.Printf("Ranked accounts: %s\n", accountsPath)
	fmt.Printf("Candidate pairs: %s\n", pairsPath)
	fmt.Printf("Summary: %s\n", filepath.Join(cfg.OutDir, "summary.json"))
	return nil
}

func median(xs []float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	cp := append([]float64(nil), xs...)
	sort.Float64s(cp)
	m := len(cp) / 2
	if len(cp)%2 == 1 {
		return cp[m]
	}
	return (cp[m-1] + cp[m]) / 2
}

func resolveProfiles(dids []string) map[string]Profile {
	out := make(map[string]Profile)
	client := &http.Client{Timeout: 30 * time.Second}
	const batchSize = 25

	for start := 0; start < len(dids); start += batchSize {
		end := start + batchSize
		if end > len(dids) {
			end = len(dids)
		}
		u, _ := url.Parse("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfiles")
		q := u.Query()
		for _, did := range dids[start:end] {
			q.Add("actors", did)
		}
		u.RawQuery = q.Encode()

		var body []byte
		ok := false
		for attempt := 0; attempt < 4; attempt++ {
			resp, err := client.Get(u.String())
			if err == nil {
				body, err = io.ReadAll(resp.Body)
				resp.Body.Close()
				if err == nil && resp.StatusCode/100 == 2 {
					ok = true
					break
				}
				if resp.StatusCode != 429 && resp.StatusCode/100 != 5 {
					break
				}
			}
			time.Sleep(time.Duration(1<<attempt) * time.Second)
		}
		if !ok {
			fmt.Fprintf(os.Stderr, "warning: profile resolution failed for batch %d-%d\n", start, end)
			continue
		}
		var pr ProfilesResponse
		if err := json.Unmarshal(body, &pr); err != nil {
			fmt.Fprintln(os.Stderr, "warning: profile JSON:", err)
			continue
		}
		for _, p := range pr.Profiles {
			out[p.DID] = p
		}
		time.Sleep(100 * time.Millisecond)
	}
	return out
}
