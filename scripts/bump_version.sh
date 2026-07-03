#!/usr/bin/env bash
# Bump the project version (pyproject.toml + package.json), tag, commit, and push.
#
# Usage: scripts/bump_version.sh <major|minor|patch>

set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^(major|minor|patch)$ ]]; then
    echo "Usage: $0 <major|minor|patch>" >&2
    exit 1
fi

part="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Working tree is not clean. Commit or stash changes first." >&2
    exit 1
fi

current_version=$(awk -F'"' '/^version = /{print $2; exit}' pyproject.toml)
if [[ -z "$current_version" ]]; then
    echo "Could not find version in pyproject.toml" >&2
    exit 1
fi

IFS='.' read -r major minor patch <<< "$current_version"

case "$part" in
    major)
        major=$((major + 1)); minor=0; patch=0
        ;;
    minor)
        minor=$((minor + 1)); patch=0
        ;;
    patch)
        patch=$((patch + 1))
        ;;
esac

new_version="${major}.${minor}.${patch}"
tag="v${new_version}"

if git rev-parse "$tag" >/dev/null 2>&1; then
    echo "Tag $tag already exists." >&2
    exit 1
fi

echo "Bumping version: $current_version -> $new_version"

# pyproject.toml
sed -i.bak -E "s/^version = \"${current_version}\"/version = \"${new_version}\"/" pyproject.toml
rm -f pyproject.toml.bak

# package.json
npm version "$new_version" --no-git-tag-version --allow-same-version >/dev/null

# keep uv.lock's recorded project version in sync
if command -v uv >/dev/null 2>&1 && [[ -f uv.lock ]]; then
    uv lock --offline >/dev/null 2>&1 || uv lock >/dev/null 2>&1 || true
fi

git add pyproject.toml package.json
[[ -f uv.lock ]] && git add uv.lock

git commit -m "Bump version to ${new_version}"
git tag -a "$tag" -m "$tag"

git push origin HEAD
git push origin "$tag"

echo "Done: ${current_version} -> ${new_version} (tag ${tag}), pushed."
