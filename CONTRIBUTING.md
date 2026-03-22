# Contributing to SOPHIA

Thank you for your interest in contributing to SOPHIA. This document explains
how to contribute and what to expect.

---

## Before You Contribute

All contributions to SOPHIA repositories are governed by the
[SOPHIA Contributor License Agreement (CLA)](CLA.md).

**By submitting a pull request you confirm that you have read the CLA and
agree to its terms.** No separate signature is required — your PR submission
constitutes your agreement.

Please read the CLA before submitting anything. Key points:

- You retain copyright of your own contribution
- You grant Scott Williams a perpetual license to use, modify, and relicense
  your contribution, including under commercial terms in the future
- You warrant that you own the code you are submitting

If you are not comfortable with these terms, please do not submit a
contribution.

---

## What to Contribute

Good candidates for contribution:

- Bug fixes with a clear description of the problem and solution
- Performance improvements with before/after context
- New device support or platform compatibility (e.g. additional activity
  sensor normalization, new BMC vendors)
- Translation files for additional languages
- Documentation improvements

Please open an issue before starting work on a significant new feature so we
can discuss whether it fits the project's direction before you invest time in
it.

---

## What Not to Contribute

- Code that introduces hardcoded personal data, credentials, or IPs
- Dependencies that are not compatible with Home Assistant's requirements
- Features that require cloud services or external APIs (SOPHIA is local-first)
- Code copied from other projects without clear license compatibility

---

## Pull Request Guidelines

1. Fork the repository and create a branch from `main`
2. Keep PRs focused — one fix or feature per PR
3. All Python files must:
   - Have `# -*- coding: utf-8 -*-` as the first line
   - Pass `grep -Pn '[^\x00-\x7F]' file.py` with zero results (ASCII only)
   - Follow the existing code style
4. Update `manifest.json` version if your change warrants it
5. Test your changes against Home Assistant 2024.4.0 or later
6. Write a clear PR description explaining what changed and why

---

## Code Style

- Follow existing patterns in the file you are modifying
- Use `_LOGGER.debug/info/warning/error` for logging — no `print()`
- No hardcoded IPs, hostnames, entity IDs, or personal data of any kind
- Keep strings ASCII-only — no special characters in comments or string literals

---

## Reporting Issues

Open a GitHub issue with:
- Your Home Assistant version
- Your SOPHIA module version
- Relevant log output (Settings > System > Logs, filter by `sophia`)
- Steps to reproduce

---

## Questions

Open a GitHub Discussion or reach out via
[Scott.J.Williams14@gmail.com](mailto:Scott.J.Williams14@gmail.com).

---

*All contributions are subject to the [SOPHIA CLA](CLA.md) and the
[PolyForm Noncommercial License](LICENSE).*
