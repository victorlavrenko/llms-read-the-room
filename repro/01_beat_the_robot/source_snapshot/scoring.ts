import { sourceToXY } from "./randomization";
import type {
  ExperimentDataset,
  RobotRoundScore,
  TweetSource,
  XY,
} from "./types";

interface ScorableTrial {
  goldSource: TweetSource;
  xSource: TweetSource;
  robotPrediction?: XY;
}

export function scoreAnswer(trial: ScorableTrial, prediction: XY): boolean {
  return sourceToXY(trial.goldSource, trial.xSource) === prediction;
}

export function scoreRound(trials: ScorableTrial[], predictions: XY[]) {
  if (trials.length !== predictions.length) {
    throw new Error("Every assigned trial must have exactly one human answer");
  }

  let humanScore = 0;
  let robotScore = 0;
  trials.forEach((trial, index) => {
    if (scoreAnswer(trial, predictions[index] as XY)) humanScore += 1;
    if (trial.robotPrediction && scoreAnswer(trial, trial.robotPrediction)) {
      robotScore += 1;
    }
  });
  return { humanScore, robotScore, total: trials.length };
}

export function scoreRobotModels(
  dataset: ExperimentDataset,
  trials: Array<ScorableTrial & { pairId: number }>,
): RobotRoundScore[] {
  const pairs = new Map(dataset.pairs.map((pair) => [pair.pairId, pair]));

  return dataset.robotModels
    .map((model) => {
      let answered = 0;
      let correct = 0;
      let nonCloseAnswered = 0;
      let nonCloseCorrect = 0;
      for (const trial of trials) {
        const pair = pairs.get(trial.pairId);
        if (!pair) {
          throw new Error(`Frozen pair ${trial.pairId} is unavailable`);
        }
        const prediction = pair.robotPredictions[model.id];
        const closeCall = pair.robotCloseCalls[model.id];
        if (!prediction || typeof closeCall !== "boolean") {
          throw new Error(
            `Frozen model data is incomplete for ${model.id} on pair ${trial.pairId}`,
          );
        }
        answered += 1;
        const isCorrect = scoreAnswer(trial, prediction);
        if (isCorrect) correct += 1;
        if (!closeCall) {
          nonCloseAnswered += 1;
          if (isCorrect) nonCloseCorrect += 1;
        }
      }
      return {
        modelId: model.id,
        displayName: model.displayName,
        correct,
        answered,
        nonCloseCorrect,
        nonCloseAnswered,
        total: trials.length,
        benchmarkAccuracy: model.benchmarkAccuracy,
      };
    })
    .sort(
      (left, right) =>
        right.correct / Math.max(right.answered, 1) -
          left.correct / Math.max(left.answered, 1) ||
        right.answered - left.answered ||
        right.benchmarkAccuracy - left.benchmarkAccuracy ||
        left.displayName.localeCompare(right.displayName),
    );
}

export function chooseFeaturedRobot(scores: RobotRoundScore[]) {
  const complete = scores.filter((score) => score.answered === score.total);
  const candidates = complete.length > 0 ? complete : scores;
  const featured = [...candidates].sort(
    (left, right) =>
      right.correct - left.correct ||
      right.benchmarkAccuracy - left.benchmarkAccuracy ||
      left.displayName.localeCompare(right.displayName),
  )[0];
  if (!featured) throw new Error("At least one robot score is required");
  return featured;
}
