#!/usr/bin/env bash
# This starts the QA workflow, it expects as asingle argument (compulsory) the PR number
# It creates the branch for the PR, rebase it with main and push it to ceph-ci.
# It assumes the origin remote point has been refreshed with the latest.
#
# Usage: ./gitqa.sh <pr_number> [<base_branch>]
# Example: ./gitqa.sh 12345 main
# Example: ./gitqa.sh 12345 (defaults to main)

set -e
# Assumes a valid git ceph checkout
git co main
git fetch origin
git pull #origin main
git submodule update --init --recursive --progress
#Argument is compulsory, if not provided, exit with error
if [ -z "$1" ]; then
  echo "Error: PR number is required."
  echo "Usage: ./gitqa.sh <pr_number> [<base_branch>]"
  exit 1
fi
PR_NUMBER=$1
BASE_BRANCH=${2:-main}
# Use current git user id.eg perezjos
PR_BRANCH="wip-perezjos-crimson-only-$(date +%d-%m-%Y)-PR${PR_NUMBER}"

echo "Creating and checking out branch $PR_BRANCH"
git fetch upstream pull/${PR_NUMBER}/head:${PR_BRANCH}
#git checkout -b $PR_BRANCH
git switch ${PR_BRANCH}
#Normally would use -i for interactive, but since we need it to be automated, we will use the default option to rebase all commits.
git rebase main && git push -f ceph-ci ${PR_BRANCH}

