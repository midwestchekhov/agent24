# Tool-Use Curricula Improve Long-Horizon Agent Reliability Independently of Base Model Scale

D. Ricci, S. Nakamura, P. Oyelaran, H. Bruckner — Cascade Systems Lab (preprint, not peer reviewed)

## Abstract

We introduce CURRIC-T, a staged tool-use curriculum for training language model agents on long-horizon software tasks. Across three base models spanning 8B, 32B, and 120B parameters, curriculum-trained agents complete 62.1%, 68.4%, and 71.9% of tasks on our held-out suite, compared with 38.7%, 51.2%, and 63.3% for the same base models trained on an equal token budget of unordered trajectories. The relative gain from curriculum shrinks with scale but remains positive at every scale tested, and the largest gains appear on tasks requiring more than 12 tool calls. We argue that trajectory ordering is a distinct and additive axis of agent capability rather than a proxy for additional training compute.

## 1. Introduction

Language model agents that operate over long horizons must interleave planning, tool invocation, and recovery from failed calls. Reported progress on agent benchmarks has largely tracked base model scale, which has led to a widely repeated claim that agent reliability is essentially a downstream consequence of general capability and that training recipes matter mainly at small scale.

We test that claim directly by holding the training token budget fixed and varying only the ordering and composition of the trajectories. If ordering is a proxy for compute, matched-budget curricula should show no advantage. If ordering is a distinct axis, the advantage should persist across scales even as its magnitude changes.

## 2. Related work

Curriculum learning has a long history in supervised settings, where results are mixed and frequently fail to replicate under matched-compute controls. In the agent setting, staged training has been reported to help with tool schema adherence, but published comparisons often vary token budget, sampling temperature, and evaluation harness simultaneously, which makes attribution difficult.

Benchmark contamination is an additional confound specific to this setting. Software agent suites are typically built from public repositories, and the pretraining corpora of the base models are not disclosed in enough detail to rule out overlap.

## 3. Method

### 3.1 Curriculum design

CURRIC-T defines four stages. Stage 1 contains single-tool tasks with deterministic verifiers. Stage 2 introduces two-tool composition with an explicit intermediate state. Stage 3 introduces failure injection, in which 20% of tool calls return errors that the agent must diagnose and retry. Stage 4 contains full-length tasks drawn from the training split of our suite, with a median of 19 tool calls per successful trajectory.

Stage transitions are triggered by a fixed success-rate threshold of 0.7 on a stage-internal validation set, evaluated every 2,000 optimizer steps. Trajectories are never repeated across stages.

### 3.2 Training

All models are trained with supervised fine-tuning on filtered successful trajectories followed by a single round of rejection-sampled self-improvement. The total token budget is fixed at 4.1B trajectory tokens for every condition, including the unordered control, which draws from the identical pooled trajectory set with the stage labels removed and the order shuffled. Sampling temperature at training-time rollout is 1.0 for all conditions; evaluation uses greedy decoding.

Base models are an 8B, a 32B, and a 120B open-weight model from the same family and pretraining recipe, which allows the scale comparison to hold the pretraining corpus fixed.

### 3.3 Evaluation suite

The held-out suite contains 480 tasks over 31 repositories, constructed from commits merged after the base models' stated pretraining cutoff. Each task provides a repository snapshot, a natural-language issue, and a hidden test suite. A task counts as complete only if the hidden tests pass and no test file is modified. Each task is run with 5 independent seeds and we report mean completion rate.

## 4. Results

Curriculum-trained agents complete 62.1% (8B), 68.4% (32B), and 71.9% (120B) of held-out tasks. Matched-budget unordered controls complete 38.7%, 51.2%, and 63.3%. The absolute gap is 23.4, 17.2, and 8.6 points respectively; the gap therefore narrows monotonically with scale but does not close at the largest scale we could train.

Stratifying by trajectory length, tasks requiring 12 or fewer tool calls show a gap of 6.1 points averaged across scales, while tasks requiring more than 12 tool calls show a gap of 21.8 points. Error analysis attributes most of the difference to recovery behavior: unordered controls abandon a task after a failed tool call in 34% of failures, versus 11% for curriculum-trained agents.

Removing Stage 3 failure injection while holding budget fixed reduces the 32B curriculum result from 68.4% to 58.9%, which is the single largest ablation effect we observe. Removing Stage 1 reduces it to 66.8%.

We ran a contamination probe in which we regenerated 60 tasks from repositories with no public history before the cutoff. Completion rates on this subset were 59.8% (curriculum) and 47.4% (control) at 32B, preserving the direction and most of the magnitude of the effect.

Variance across the 5 seeds was 1.4 points or less in every condition.

## 5. Discussion

The matched-budget design rules out the simplest alternative explanation, that curricula help only by acting as extra compute. Within the family of base models tested and the suite constructed here, ordering has an effect that is separable from scale.

The claim is nonetheless bounded in several ways that we want to be explicit about. All three base models come from a single family and pretraining recipe, so "independent of scale" is demonstrated across a scale axis within one recipe and not across architectures or data mixtures. The evaluation suite is software-engineering tasks with executable verifiers; agent domains without a deterministic verifier, such as open-ended research or multi-party negotiation, are outside what we measured. The contamination probe reduces but does not eliminate the possibility of overlap, because we cannot inspect the pretraining corpus.

Finally, the narrowing of the gap with scale is itself only observed over a 15x parameter range. Extrapolating the trend to conclude that the gap closes at some larger scale is not supported by three points, and we do not make that claim.

## 6. Conclusion

Staged tool-use curricula improve long-horizon agent completion rates at matched training token budget across three model scales, with the effect concentrated in long-horizon recovery behavior. Whether the effect survives across pretraining recipes and in domains without executable verification remains open.

## Acknowledgments

We thank the Cascade infrastructure team for cluster time and three anonymous reviewers of an earlier draft for the matched-budget control design.

## References

1. Bengio Y, Louradour J, Collobert R, Weston J. Curriculum learning. ICML, 2009.
2. Hoffmann J, et al. Training compute-optimal language models. NeurIPS, 2022.
3. Kaur A, Weisman T. Matched-compute controls in curriculum learning replications. TMLR, 2023.
4. Sung Y, Oyelaran P. Failure recovery as a distinct agent capability. Preprint, 2024.
5. Marchetti L, Iyer R. Benchmark contamination in code agent evaluation. Preprint, 2024.
6. Nakamura S, Bruckner H. Verifier-grounded task construction for agent suites. Preprint, 2024.
7. Whitfield C, Danso E. Scaling laws for tool use. Preprint, 2023.
8. Ostrowski J, Lam K. Seed variance and reporting practice in agent benchmarks. Preprint, 2024.
