export function buildSelfCheckPrompt(tweetX: string, tweetY: string): string {
  return `These two tweets were posted by the same account, about the same link, in the early 2010s.

Which one do you think received more retweets at the time?

X:
${tweetX}

Y:
${tweetY}

You must choose either X or Y. Also indicate whether your choice was a close call.

Finish with exactly these two lines:

Prediction: X or Y
Close call: Yes or No`;
}
