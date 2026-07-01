# Crimson Workload Flow

This document describes a Crimson workload flow spanning three related artifact tracks:

- Redmine tracker
- GitHub pull requests
- Jira artifacts

The diagram below is written in Mermaid so it can be rendered by platforms that support Mermaid in Markdown, including GitHub and compatible wiki/rendering environments.

## Workflow Diagram

```mermaid
flowchart LR
  %% Row 1: Redmine tracker
  subgraph REDMINE["Redmine tracker"]
    direction LR
    tracker_new["New issue / enhancement tracker ticket"]
    tracker_done["Tracker completed / closed"]
  end

  %% Row 2: GitHub PRs
  subgraph GITHUB["GitHub PRs"]
    direction LR
    pr_new(("New / Draft PR"))
    pr_ready(("Ready"))
    pr_qa(("QA in Progress"))
    pr_gated(("Gated"))
    pr_tested(("Tested"))
    pr_done(("Done (upstream)"))
    qa_report["QA report"]

    pr_new -->|review| pr_ready
    pr_ready -->|needs QA| pr_qa
    pr_qa -->|fail| pr_gated
    pr_qa -->|pass| pr_tested
    pr_gated -->|fix| pr_ready
    pr_tested -->|merge| pr_done
    pr_tested -. produces .-> qa_report
  end

  %% Row 3: Jira artifacts
  subgraph JIRA["Jira artifacts"]
    direction LR
    jira_new["New"]
    jira_progress["In progress"]
    jira_done["Completed"]

    jira_new --> jira_progress --> jira_done
  end

  %% Cross-row relationships
  tracker_new --> pr_new
  pr_done --> tracker_done
```

## Flow Description

### 1. Redmine tracker
The Redmine tracker row starts with a single state:

- **New issue / enhancement tracker ticket**

and ends with:

- **Tracker completed / closed**

This represents the lifecycle of the top-level tracker item.

### 2. GitHub PR lifecycle
The GitHub PR row models the main engineering workflow:

- A PR starts in **New / Draft PR**
- It can be created as a result of a tracker ticket, but it may also be created independently
- From **New / Draft PR**, a transition labelled **review** moves it to **Ready**
- From **Ready**, a transition labelled **needs QA** moves it to **QA in Progress**
- From **QA in Progress**:
  - **fail** leads to **Gated**
  - **pass** leads to **Tested**
- A successful QA pass also produces a separate event artifact:
  - **QA report**
- From **Gated**, a transition labelled **fix** returns the PR to **Ready**
- From **Tested**, a transition labelled **merge** moves it to **Done (upstream)**
- Once the PR is **Done (upstream)**, it connects back to the Redmine tracker completion state

### 3. Jira artifacts
The Jira row is a simple left-to-right progression:

- **New**
- **In progress**
- **Completed**

This row is intentionally lightweight and shown as a simple artifact progression.

## Notes

- Circular nodes are used for GitHub PR states.
- The **GA report** is shown as a rectangular node to distinguish it from state nodes.
- The diagram is kept in standard Mermaid `flowchart` syntax for broad Markdown renderer compatibility.
