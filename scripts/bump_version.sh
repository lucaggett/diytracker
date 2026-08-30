#!/usr/bin/env bash
# Bump the project version (pyproject.toml + package.json), tag, commit, and push.
#
# Usage: scripts/bump_version.sh <major|minor|patch|X.Y.Z>
#
# The explicit X.Y.Z form exists because the changelog and the version can
# drift apart: releases 0.57.0 through 0.59.0 were written up but never
# bumped, and running `minor` four times to catch up would have minted four
# tags all pointing at the same tree. It only moves forward.

set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^(major|minor|patch|[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    echo "Usage: $0 <major|minor|patch|X.Y.Z>" >&2
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
    *)
        IFS='.' read -r major minor patch <<< "$part"
        ;;
esac

new_version="${major}.${minor}.${patch}"
tag="v${new_version}"

# Guard the explicit form against typos that would move the version backwards.
if [[ "$(printf '%s\n%s\n' "$current_version" "$new_version" | sort -V | tail -1)" != "$new_version" \
      || "$new_version" == "$current_version" ]]; then
    echo "Refusing to go from $current_version to $new_version — versions only move forward." >&2
    exit 1
fi

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

git commit -a -m "Bump version to ${new_version}"
git tag -a "$tag" -m "$tag"

git push origin HEAD
git push origin "$tag"

echo "Done: ${current_version} -> ${new_version} (tag ${tag}), pushed."
