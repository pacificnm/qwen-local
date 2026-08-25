# GitHub Integration Feature: VS Code-like Source Control Panel

**Version:** 1.0 (planning phase)  
**Status:** Specification complete — ready for implementation  
**Related:** `FEATURES/FILE_TREE_IMPLEMENTATION.md`

---

## 1. Executive Summary

This feature extends the existing 3-pane chat UI with a **VS Code-like GitHub integration panel**. It replaces the simple dropdown branch picker with a full source control experience including:
- Branch management (list, create, delete)
- Commit history view with diffs
- Pull request creation workflow
- Merge/Conflict handling

The feature integrates with the existing backend API (`/api/repos/{id}/`) and Monaco Editor for diff viewing.

---

## 2. Why This Feature?

| Problem | Solution |
|---------|----------|
| Dropdown branch picker is limited | Quick pick + full branch management UI |
| No commit history visibility | Sidebar with commit list and diffs |
| Manual PR creation steps | Dedicated PR creation modal |
| No merge/Conflict feedback | Visual indicators in sidebar |

---

## 3. Technical Constraints & Dependencies

### Backend API (Existing Pattern)
```typescript
// backend/app/api/edits.py
GET  /api/repos/{repo_id}/branches      # List branches
POST /api/repos/{repo_id}/branches      # Create branch
DELETE /api/repos/{repo_id}/branches/{branch}  # Delete branch
GET  /api/repos/{repo_id}/commits       # Commit history
POST /api/repos/{repo_id}/pull-requests # Create PR
```

### Frontend State Management
| Store | Purpose | Integration |
|-------|---------|-------------|
| `githubStore` | Branch/commit/PR state | Zustand actions |
| `editorStore` | Monaco diff view | File changes trigger |

### Existing Tools to Leverage
- `gitops.list_file_paths()` - File listing (for commit diffs)
- `gitops.commit_workspace()` - Commit to GitHub
- `backend/app/api/edits.py` - API endpoints

---

## 4. UI Layout & Component Structure

```
┌─────────────────────────────────────────────────────────────┐
│  Header: [Repo] [Branch Picker ▼] [User]                    │
├──────────┬──────────────────────────────────────────────────┤
│          │                                                  │
│ Chat    │   Monaco Editor (with diff view)                 │
│ Pane     │                                                  │
│         │                                                  │
│ File    │                                                  │
│ Tree    │                                                  │
│         │                                                  │
├──────────┴──────────────────────────────────────────────────┤
│  GitHub Sidebar (Branches, Commits, PRs)                   │
└─────────────────────────────────────────────────────────────┘
```

### Component Hierarchy

| Component | Location | Purpose |
|-----------|----------|---------|
| `GithubSidebar.tsx` | `frontend/src/components/` | Main sidebar (branches/commits/PRs) |
| `BranchPicker.tsx` | `frontend/src/components/` | Quick pick for branch selection |
| `CommitHistory.tsx` | `frontend/src/components/` | Recent commits with diffs |
| `PRCreationModal.tsx` | `frontend/src/components/` | PR creation dialog |

---

## 5. Implementation Phases

### Phase 1: Branch Picker (Quick Pick)
- Replace dropdown in `EditorPane.tsx` header
- Keyboard shortcuts (↑↓ to navigate, Enter to select)
- Show branch name + commit count
- Click to switch branches

**Backend:** `GET /api/repos/{id}/branches`  
**Frontend:** `BranchPicker.tsx`

### Phase 2: GitHub Sidebar
- Branch list with create/delete buttons
- Commit history with diff preview
- PR creation button
- Merge conflict indicator

**Backend:** `POST /api/repos/{id}/branches`, `DELETE /api/repos/{id}/branches/{branch}`, `GET /api/repos/{id}/commits`  
**Frontend:** `GithubSidebar.tsx`

### Phase 3: PR Creation UI
- Form for PR title, body, base/merge branch
- Auto-fill from commit message
- Preview diff before creating

**Backend:** `POST /api/repos/{id}/pull-requests`  
**Frontend:** `PRCreationModal.tsx`

### Phase 4: Merge/Conflict Handling
- Visual indicators in sidebar
- Conflict resolution UI (future enhancement)

---

## 6. Key Considerations

| Aspect | Recommendation | Reason |
|--------|---------------|--------|
| **Performance** | Virtualize long commit lists | Large repos can have thousands of commits |
| **UX Details** | Keyboard shortcuts + mouse support | Match VS Code experience |
| **Error Handling** | Show GitError messages in UI | Clear feedback for failed operations |
| **State Sync** | Zustand stores + Monaco events | Keep editor and sidebar in sync |

---

## 7. References

| Document | Purpose |
|----------|---------|
| `FEATURES/FILE_TREE_IMPLEMENTATION.md` | Related feature (file navigation) |
| `backend/app/api/edits.py` | API endpoints for branches/commits |
| `frontend/src/components/EditorPane.tsx` | Integration point for branch picker |
| `frontend/src/stores/` | Zustand store pattern |

---

## 8. Next Steps

1. **Review** this specification
2. **Create** `FEATURES/GITHUB_INTEGRATION_IMPLEMENTATION.md` with detailed implementation guide
3. **Implement** Phase 1 (Branch Picker) first
4. **Test** integration with existing Monaco Editor

---

*Document created by Qwen code assistant on 2026-08-25*
