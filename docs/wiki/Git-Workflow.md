# Git Workflow

Nobody pushes to `main` directly. Every change goes on a branch and through a pull request, which the owner reviews
and merges.

**Merging is not deploying.** A merge into `main` reaches the [Staging](Staging) site. Production only changes when
the owner publishes a release ([Deployment](Deployment)).

## 1. Branch

```bash
git checkout main
git pull origin main
git checkout -b 154-upload-feedback        # <issue number>-<short description>
```

## 2. Change, test, commit

Work against <http://localhost:8000> ([Getting Started](Getting-Started)) and run the tests ([Testing](Testing)).
Commit in steps that make sense on their own. Write messages that say what changed and why:

```
Streak: a day only counts once that day's goal is reached (#423)

Players kept a streak by answering one beetle a day...
```

- New Python or JS dependencies need `docker compose build` or `pixi run install-js`; see Getting Started.
- New model fields need a migration; see [Migrations](#migrations) below.
- Template changes with new Tailwind classes need `pixi run build-css`. Commit the rebuilt `style.css`.

## 3. Push and open a pull request

```bash
git push -u origin 154-upload-feedback
```

Open the PR into `main`. In the description:
- **What** it does, in a few bullets;
- **Why**, with `Closes #154`;
- **How you tested** it: which tests you added, and what you clicked through, with a screenshot for anything visible;
- **Anything the owner must do** after deploying: a new setting in `.env.prod`, or a one-off command.

CI must be green before review.

## Keeping a PR mergeable

- **Keep it small:** one topic per PR. If a task has parts, split it into several PRs.
- **Stacking:** if PR B needs PR A, branch B off A and set B's base to A's branch. Say so in both descriptions
  ("merge #440 first"). GitHub retargets B to `main` when A is merged and its branch is deleted.
- **Catching up with `main`:** merge `main` into your branch (`git fetch origin && git merge origin/main`). Don't rebase
  or force-push a branch someone else may have checked out.
- **Conflicts in generated files:** resolve conflicts in `pixi.lock` or `package-lock.json` by re-running the tool
  (`pixi install`, `npm install`), not by hand. For `style.css`, run `pixi run build-css` after the merge.

## Migrations

Production runs `migrate` on every deploy, against live data, while the previous version is still serving. So:

- **Only add:** new tables, new nullable columns, or columns with defaults. Old code must keep working on the new
  schema.
- **Removing data takes two releases:** first stop using the column, then remove it in a later PR, with
  `DESTRUCTIVE_OK = "why it is safe"` on the migration.
- **Data migrations (`RunPython`):** set `DATA_MIGRATION_REVIEWED = "what it reads and writes"`, and make them safe to
  run twice.
- **Numbering clashes:** if two open PRs both add migration `00NN`, the second one to merge renumbers its migration
  and points `dependencies` at the first one's. Run
  `python manage.py makemigrations --check` afterwards; it should say "No changes detected".

`test_deploy_safety.py` enforces these rules ([Testing](Testing#guards-you-may-run-into)).
