# VS Code-like GitHub Integration Specification

## Executive Summary

This document specifies the implementation of a **VS Code-like source control panel** that replaces the current simple dropdown for branch selection. The goal is to provide:

- Visual branch management (list, create, delete)
- Commit history view with diffs
- PR creation UI
- Activity Bar pattern (like VS Code's source control panel)

---

## Key Facts & Constraints

### Backend API Extensions Required

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/repos/{id}/branches` | GET | List all branches |
| `/api/repos/{id}/branches` | POST | Create new branch |
| `/api/repos/{id}/branches/{branch}` | DELETE | Delete branch |
| `/api/repos/{id}/commits` | GET | Commit history |
| `/api/repos/{id}/pull-requests` | POST | Create PR |

**Existing Backend Integration:**
- `gitops.open_pull_request()` - Already exists in `backend/app/repos/gitops.py`
- `gitops.commit_workspace()` - For committing changes
- `gitops.commit_file()` - For single file commits

### Frontend Files to Modify/Create

| File | Action | Reason |
|------|--------|---------|
| `frontend/src/components/GithubSidebar.tsx` | **Create** | Main sidebar component |
| `frontend/src/components/BranchPicker.tsx` | **Create** | Quick pick for branch selection |
| `frontend/src/components/CommitHistory.tsx` | **Create** | Recent commits view |
| `frontend/src/store/github.ts` | **Create** | State management for GitHub data |

### Technical Constraints

1. **Performance:** Debounce API calls (500ms) to avoid rate limiting
2. **UX:** Keyboard shortcuts (↑↓ to navigate, Enter to select)
3. **State Management:** Use Zustand store pattern (already in use)
4. **Error Handling:** Graceful degradation if API fails

---

## Implementation Plan

### Phase 1: Branch Picker (Quick Pick)

**Goal:** Replace simple dropdown with VS Code-style quick pick.

**Component Structure:**
```typescript
// frontend/src/components/BranchPicker.tsx
export function BranchPicker({ repoId, onBranchSelect }) {
  const [branches, setBranches] = useState([]);
  const [selectedBranch, setSelectedBranch] = useState('main');
  
  // Fetch branches from backend
  useEffect(() => {
    fetchBranches(repoId).then(setBranches);
  }, [repoId]);

  return (
    <div className="quick-pick">
      <select value={selectedBranch} onChange={(e) => onBranchSelect(e.target.value)}>
        {branches.map(branch => (
          <option key={branch.name} value={branch.name}>
            {branch.name} ({branch.commit_count} commits)
          </option>
        ))}
      </select>
    </div>
  );
}
```

**Backend API:**
- `GET /api/repos/{id}/branches` - List branches
- Return format: `{ name, commit_sha, is_current }`

### Phase 2: GitHub Sidebar (Branches + Commits)

**Goal:** Create Activity Bar-style sidebar with branch list and commit history.

**Component Structure:**
```typescript
// frontend/src/components/GithubSidebar.tsx
export function GithubSidebar({ repoId }) {
  const [branches, setBranches] = useState([]);
  const [commits, setCommits] = useState([]);
  
  // Fetch data from backend
  useEffect(() => {
    fetchBranches(repoId).then(setBranches);
    fetchCommits(repoId).then(setCommits);
  }, [repoId]);

  return (
    <div className="github-sidebar">
      <h3>Branches</h3>
      <ul>
        {branches.map(branch => (
          <li key={branch.name}>
            <button onClick={() => switchBranch(branch.name)}>
              {branch.name}
            </button>
          </li>
        ))}
      </ul>
      
      <h3>Commits</h3>
      <ul>
        {commits.map(commit => (
          <li key={commit.sha}>
            <a href={`#commit-${commit.sha}`}>
              {commit.message.slice(0, 50)}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

**Backend API:**
- `GET /api/repos/{id}/branches` - List branches
- `GET /api/repos/{id}/commits` - Commit history (last 20)

### Phase 3: PR Creation UI

**Goal:** Create modal dialog for PR creation.

**Component Structure:**
```typescript
// frontend/src/components/PRCreationModal.tsx
export function PRCreationModal({ repoId, baseBranch, onPRCreated }) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  
  // Handle PR creation
  async function createPR() {
    await fetch('/api/repos/{id}/pull-requests', {
      method: 'POST',
      body: JSON.stringify({
        repo_id: repoId,
        base_ref: baseBranch,
        head_ref: 'qwen-assist/pr',
        title: title,
        body: body
      })
    });
    onPRCreated();
  }

  return (
    <div className="pr-modal">
      <h3>Create Pull Request</h3>
      <input 
        type="text" 
        placeholder="PR Title" 
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea 
        placeholder="PR Body" 
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <button onClick={createPR}>Create PR</button>
    </div>
  );
}
```

**Backend API:**
- `POST /api/repos/{id}/pull-requests` - Create PR
- Return: `{ html_url, head, base }`

### Phase 4: Merge/Conflict Handling

**Goal:** Handle merge operations and conflicts.

**Component Structure:**
```typescript
// frontend/src/components/MergeModal.tsx
export function MergeModal({ repoId, branch, onMergeComplete }) {
  // Show merge progress, errors, or success message
}
```

---

## Key Considerations

### 1. Performance
- **Problem:** Large number of branches/commits causes slow rendering
- **Solution:** 
  - Debounce API calls (500ms)
  - Limit commit history to last 20 commits
  - Virtualize branch list if >100 branches

### 2. UX Details
- **Keyboard Shortcuts:**
  - `↑`/`↓`: Navigate up/down in quick pick
  - `Enter`: Select branch
  - `Esc`: Close modal
- **Loading States:** Show spinner during fetch
- **Error Handling:** Graceful degradation if API fails

### 3. Integration Points
- **Zustand Store:** Use `githubStore` for reactive updates
- **Backend APIs:** Leverage existing `/api/repos/{id}/` pattern
- **GitOps:** Integrate with `gitops.open_pull_request()` for PR creation

---

## References

| Document | Link | Purpose |
|----------|------|---------|
| Backend GitOps | `backend/app/repos/gitops.py` | Branch/PR operations |
| Editor Pane | `frontend/src/components/EditorPane.tsx` | Branch picker integration |
| Zustand Store | `frontend/src/store/editor.ts` | State management pattern |

---

**Status:** ⏳ Ready for Phase 1 Implementation

**Next Step:** Create `BranchPicker.tsx` component to replace dropdown in `EditorPane.tsx`.
