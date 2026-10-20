---
name: release-process
description: Use when preparing a release, tag, or GitHub release note. Keep the release summary short, clear, and written in English.
---

# Release process

When creating or updating a release:

1. Keep the release description short and readable.
2. Write the summary in English.
3. Prefer a compact format such as:
   - 2 to 4 bullet points with the main changes
   - one short note explaining why the release exists
4. Do not write long narrative text or detailed changelogs unless the user explicitly asks for them.
5. Always create a pull request before merging any code change into `main`.

## Example

```md
## Highlights
- Add support for monitoring UniFi log files.
- Set normal priority for Pushover alerts.
- Keep default model selection aligned with Mammouth recommendations.

## Notes
This release improves the default monitoring coverage and keeps notifications at a standard priority level.
```
