import type {
  AssignedTrial,
  FrozenPair,
  RoundNumber,
  TweetSource,
  XY,
} from "./types";

function stableHash(value: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}

export function sourceToXY(source: TweetSource, xSource: TweetSource): XY {
  return source === xSource ? "X" : "Y";
}

export function xyToSource(prediction: XY, xSource: TweetSource): TweetSource {
  if (prediction === "X") return xSource;
  return xSource === "first" ? "second" : "first";
}

function toAssignedTrial(
  pair: FrozenPair,
  round: RoundNumber,
  position: number,
): AssignedTrial {
  return {
    ...pair,
    round,
    position,
    tweetX: pair.xSource === "first" ? pair.firstTweet : pair.secondTweet,
    tweetY: pair.ySource === "first" ? pair.firstTweet : pair.secondTweet,
  };
}

export function assignRounds(
  pairs: FrozenPair[],
  seed: string,
  pairsPerRound = 20,
  roundCount = 2,
): AssignedTrial[][] {
  const required = pairsPerRound * roundCount;
  if (pairs.length < required) {
    throw new Error(
      `Dataset has ${pairs.length} pairs but ${required} are required`,
    );
  }

  const random = mulberry32(stableHash(seed));
  const shuffled = [...pairs];
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(random() * (index + 1));
    [shuffled[index], shuffled[swapIndex]] = [
      shuffled[swapIndex] as FrozenPair,
      shuffled[index] as FrozenPair,
    ];
  }

  return Array.from({ length: roundCount }, (_, roundIndex) =>
    shuffled
      .slice(roundIndex * pairsPerRound, (roundIndex + 1) * pairsPerRound)
      .map((pair, position) =>
        toAssignedTrial(pair, (roundIndex + 1) as RoundNumber, position + 1),
      ),
  );
}
