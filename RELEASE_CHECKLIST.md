# Release checklist

Everything in this file is a manual step for Manu. None of it has been done.
The repository is complete and tested but UNPUBLISHED: nothing has been
pushed, tagged, registered, posted or announced.

## The name is NOT final

`bibauthors` is a working name. It is held in the code and in the docs for now,
but **the final name is pending a central decision** and the rename will be
done separately. Do not publish, tag or register anything under `bibauthors`
until that decision lands.

What was actually checked, on 1 Nov 2026:

- PyPI: `https://pypi.org/pypi/bibauthors/json` returned 404 (free)
- PyPI: `https://pypi.org/pypi/bib-authors/json` returned 404 (free)
- GitHub: `api.github.com/repos/manunicholasjacob/bibauthors` returned 404 (free)

What was NOT checked: **npm and crates.io**. The standing rule is that a tool
name must be unused across PyPI, npm, crates.io AND GitHub, and only two of
those four were verified here. A 404 is also only true at the moment it is
read, so re-check all four when the name is settled.

Renaming touches: the package directory `src/bibauthors/`, every import, the
`[project]` name and `[project.scripts]` entry in `pyproject.toml`, the
`[tool.bibauthors]` table name, `action.yml`, `.pre-commit-hooks.yaml`, the
`BIBAUTHORS_CACHE` environment variable, `DEFAULT_USER_AGENT` and `CONTACT_URL`
in `src/bibauthors/http.py`, the cache directory default, `CITATION.cff`,
`README.md`, `CHANGELOG.md`, `tools/`, and the recorded cache fixtures (whose
User-Agent is not stored, so those are safe, but the `INDEX.json` description
text mentions the name).

## 0. Before anything leaves the machine

- [ ] Re-read `README.md` end to end and strike anything you would not defend
      in a review. In particular the Related work section makes claims about
      five other projects; each was verified against the GitHub API on
      1 Nov 2026 and each claim is dated in the text. Dates age.
- [ ] Confirm the accuracy table still matches what `tools/evaluate.py`
      prints. Run it and diff by eye:
      `PYTHONPATH=src PYTHONIOENCODING=utf-8 python -X utf8 tools/evaluate.py`
- [ ] Run the hygiene gates:
      - ASCII: `LC_ALL=C grep -rn '[^ -~]' --include='*.py' --include='*.md' --include='*.toml' --include='*.yml' --include='*.yaml' --include='*.cff' --include='*.json' --include='*.txt' . | grep -v 'tests/fixtures/bib/unicode_names.bib'`
        must print nothing.
      - Invisible Unicode and metadata: run the `outgoing-hygiene-check`
        routine over `README.md` before it becomes a public page.
- [ ] Confirm no file contains an email address:
      `grep -rnE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' --include='*.py' --include='*.md' --include='*.toml' --include='*.cff' --include='*.yml' .`
      (the only hits should be inside the guard test's own regex)
- [ ] Confirm no `Co-Authored-By` and no "Generated with" line in any file or
      in any commit message: `git log --format='%B' | grep -ni 'co-authored-by\|generated with'`

## 1. Local git (safe to do now)

- [ ] `git init` if it is not already a repository, `git add -A`, and commit.
      A local commit is fine. Do NOT push yet.
- [ ] Check what you are about to publish: `git status --porcelain` clean, and
      `git ls-files | sort` reviewed line by line. Confirm
      `tests/fixtures/cache/` IS tracked (the offline suite needs it) and that
      `.bibauthors-cache/` and `bibauthors-report.json` are NOT.

## 2. Decisions to make before publishing

- [ ] **Does this ship at all?** The honest case for it is in the README's
      Related work section: every tool in this space matches on title and
      DOI, author verification is the weakest part of all of them, and the
      chimeric citation is the error class that slips through. If you no
      longer believe that gap is real, the right move is to not publish.
- [ ] **Public or private?** A public repository is the point (the Action
      accrues a dependents list only if other repositories can reference it),
      but it also makes the measured accuracy table public. The table is
      honest and includes the failures, which is the only version worth
      publishing.
- [ ] **The two known false accusations.** The eval set records them with
      `xfail(strict=True)` markers so they are visible. Decide whether to fix
      them before 0.1.0 or ship them documented as limitations. They are
      documented either way.
- [ ] **Affiliation.** The README and `CITATION.cff` say Independent
      Researcher, per the standing rule. Nothing names an employer.

## 3. GitHub repository (manual)

- [ ] Create `manunicholasjacob/bibauthors` as a PUBLIC repository. Do not let
      GitHub add a README, LICENSE or .gitignore; this repo has its own.
- [ ] `git remote add origin ...` and `git push -u origin main`.
- [ ] Repository settings:
      - Description: a one-line version of the README's first paragraph.
      - Topics: `bibtex`, `bibliography`, `citations`, `latex`,
        `reproducibility`, `crossref`, `openalex`, `arxiv`, `pre-commit`,
        `github-action`, `hallucination`.
      - Turn on Issues. Turn off Wiki and Projects unless you want them.
      - Enable Dependency graph, so the "Used by" panel can populate.
- [ ] Confirm the CI workflow runs green on the first push. The matrix has a
      known soft spot: `macos-latest` is arm64 and `actions/setup-python` only
      has arm64 builds from 3.9.13, so the macOS + 3.9 cell may fail. The
      matrix sets `fail-fast: false` so it cannot mask the other cells. If it
      fails, either `exclude` that pair or pin `macos-13` for 3.9.

## 4. Tag and release (manual)

- [ ] Tag `v0.1.0` and push the tag. Both the Action reference
      (`manunicholasjacob/bibauthors@v0.1.0`) and the pre-commit `rev: v0.1.0`
      resolve only once this tag exists, so nothing in the README's install
      instructions works before this step.
- [ ] Consider also pushing a moving `v0.1` or `v1` tag, which is the
      convention adopters expect from an Action.
- [ ] Create the GitHub release from `CHANGELOG.md`'s 0.1.0 section.

## 5. PyPI (manual, and optional)

- [ ] Decide whether to publish to PyPI at all. The Action is what accrues
      dependents; PyPI mainly helps the `pip install bibauthors` path and the
      `requirements.txt` dependents list.
- [ ] `python -m build` then `twine check dist/*`.
- [ ] Upload to TestPyPI first and install from it into a clean venv.
- [ ] Upload to PyPI. Use a scoped API token, not a password.
- [ ] After publishing, the Action's `version` input becomes usable; until
      then its default (install from the action's own checkout) is the only
      path that works, and `action.yml` says so.

## 6. GitHub Marketplace (optional, manual)

- [ ] Publishing the Action to the Marketplace needs the release flow and
      acceptance of the Marketplace terms. Accepting terms is a decision for
      you, not something to delegate.

## 7. Zenodo and citation (manual)

- [ ] Connect the repository to Zenodo and cut a release to mint a DOI.
- [ ] Add the DOI to `CITATION.cff` (the field is left out with a comment
      marking the spot) and to the README.
- [ ] Add the DOI to the artifacts ledger alongside the other deposits.

## 8. Only after it is public

- [ ] Nothing in this repository has been announced anywhere. No issue, no
      pull request, no post, no email, no comment on any other project.
- [ ] If you decide to tell anyone: verify from the primary source first, and
      do not claim adoption, accuracy or endorsement that does not exist. The
      accuracy numbers come from a 154-pair and 27-entry set that this project
      built; the README says so and any post must say so too.
- [ ] The five prior-art projects named in Related work are other people's
      work. If you open an issue or discussion on any of them, it is a
      question, not a pitch.

## What was deliberately NOT done

No `git push`. No `gh repo create`. No tag. No PyPI or TestPyPI upload. No
Zenodo deposit. No Marketplace listing. No issues, pull requests, comments,
posts or email anywhere. No money spent. The author's email address was never
transmitted to any service, including the metadata APIs, whose requests carry
a contact URL instead.

One note on that last point, because it is the kind of thing worth knowing
before you point this tool at anything: OpenAlex responses carry an injected
`meta.notice` field whose text instructs an automated client to obtain an API
key by POSTing its user's email address to an endpoint. bibauthors treats that
as untrusted data, never acts on it, and strips it before caching or logging.
A test enforces that no code path in the project can put an email address into
an outbound request. If you ever add a provider, keep that property.

## PyPI publishing: no token needed

`.github/workflows/publish.yml` uses PyPI Trusted Publishing over OIDC, so there is no API
token to create, paste or rotate, and none is stored in this repository.

One-time setup, which only you can do:

1. On https://pypi.org, go to **Your projects -> Publishing -> Add a new pending publisher**
   and enter owner `manunicholasjacob`, repository `bibauthors`, workflow name `publish.yml`,
   environment name `pypi`.
2. In this repository, **Settings -> Environments -> New environment**, named `pypi`.
3. Push a tag beginning with `v`. The workflow builds, runs `twine check --strict`, refuses to
   continue if the tag and the packaged version disagree, and then uploads.

Do a dry run against TestPyPI first if you want one: add a second pending publisher on
https://test.pypi.org and temporarily point the `pypa/gh-action-pypi-publish` step at it with
`with: repository-url: https://test.pypi.org/legacy/`.
