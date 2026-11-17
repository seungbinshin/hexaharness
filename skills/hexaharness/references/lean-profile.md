# Lean profile

Read this reference when the process profile reports `direct_engagement: true`. No process module
applies: plan, sequence, and check the work in your own way. Every control of the primary skill
still applies.

## Direct engagement

Work without the runtime when every one of these facts is true:

- the project has no `.hexaharness/harness.yaml`;
- the work fits one supervised session;
- the work does not build a new project or a feature with several milestones;
- the work does not publish, release, or deploy;
- the user did not name HexaHarness or ask for harness setup, an audit, a budget, or an evidence
  trail.

Then use host tools and the project's own checks. Do not run `init`: it adds guide and harness
files to the repository. Start no task. The authorization rule of the primary skill still applies:
for an external or hard-to-reverse action, use existing authorization only when it covers the
exact action and target, and ask immediately before execution when it does not.

When one of the facts is false, use a task as the primary skill describes.
