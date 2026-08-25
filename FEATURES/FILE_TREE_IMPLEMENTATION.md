# VS Code-like File Tree Implementation Specification

## Executive Summary

This document specifies the implementation of a **VS Code-like file tree component** that replaces the current dropdown-based file browsing mechanism in the editor pane. The goal is to provide:

- Visual file/folder navigation
- Keyboard shortcuts (Ctrl+Click to open, Enter to edit)
- Integration with existing `gitops.list_file_paths()` backend tool
- Performance optimization for large repositories

---

## Key Facts & Constraints

### Backend Integration Points

| Tool | Location | Purpose |
|------|----------|---------|
| `gitops.list_file_paths()` | `backend/app/repos/gitops.py` | List files in a directory |
| `workspace_repo_dir()` | `backend/app/repos/gitops.py` | Map `owner/name` → `workspace/owner__name` |

**File Path Format:**
- Repo-relative paths (e.g., `src/app.py`, `README.md`)
- No absolute paths or OS-specific separators
- Backslash (`\`) must be replaced with forward slash (`/`) in path mappings

### Frontend Files to Modify/Create

| File | Action | Reason |
|------|--------|--------|
| `frontend/src/components/FileTree.tsx` | **Create** | Main file tree component |
| `frontend/src/components/EditorPane.tsx` | **Modify** | Replace dropdown with file tree |
| `frontend/src/store/editor.ts` | **Modify** | Add file path state management |

### Technical Constraints

1. **Performance:** Virtualize the tree for repos with >10,000 files
2. **UX:** Show file extensions for quick identification (`.py`, `.js`, etc.)
3. **Error Handling:** Gracefully handle missing directories or permission errors
4. **State Management:** Use Zustand store for reactive updates

---

## Implementation Plan

### Phase 1: Create `FileTree.tsx` Component

**Goal:** Build a recursive file tree component with expand/collapse functionality.

**Key Features:**
- Recursive directory structure rendering
- Expand/collapse folders on click
- Keyboard navigation (↑↓ arrows, Enter to open)
- Loading states for large directories
- Error handling for inaccessible paths

**Component Structure:**
```typescript
// frontend/src/components/FileTree.tsx
export function FileTree({ repoName, onFileSelect }) {
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  
  // Fetch files via RepoListFiles tool
  async function loadFiles(path = '') {
    const files = await fetchRepoFiles(repoName, path);
    renderTree(files, expandedFolders);
  }

  return (
    <div className="file-explorer">
      <FolderItem 
        name="root" 
        children={files} 
        onExpand={setExpandedFolders}
        onSelect={(path) => onFileSelect(path)}
      />
    </div>
  );
}
```

### Phase 2: Replace Dropdown in `EditorPane.tsx`

**Goal:** Integrate file tree into the editor pane layout.

**Changes Required:**
1. Remove existing dropdown selector
2. Add file tree component to sidebar area
3. Wire up `onFileSelect` callback to Monaco Editor
4. Add refresh button to reload files after git sync

### Phase 3: Optimization & Polish

**Goal:** Improve performance and UX for large repositories.

**Optimizations:**
- Virtualize file list rendering (only render visible items)
- Lazy load directories on demand
- Cache file paths in memory
- Add loading skeletons during fetch

---

## Key Considerations

### 1. Performance
- **Problem:** Large repos (>10k files) cause slow rendering
- **Solution:** Virtualize the tree, lazy-load directories
- **Metric:** Target <100ms render time for visible portion

### 2. UX Details
- **Keyboard Shortcuts:** 
  - `↑`/`↓`: Navigate up/down
  - `Enter`: Open file or expand folder
  - `Ctrl+Click`: Select file
- **Loading States:** Show spinner/skeleton during fetch
- **Error Handling:** Graceful degradation if directory inaccessible

### 3. Integration Points
- **Monaco Editor:** Pass selected file path to editor via props
- **Git Sync:** Refresh tree after `gitops.sync()` completes
- **Backend API:** Use existing `RepoListFiles` tool (no new endpoints needed)

---

## References

| Document | Link | Purpose |
|----------|------|---------|
| Backend GitOps | `backend/app/repos/gitops.py` | File listing implementation |
| Editor Pane | `frontend/src/components/EditorPane.tsx` | Current dropdown implementation |
| Zustand Store | `frontend/src/store/editor.ts` | State management pattern |

---

**Status:** ⏳ Ready for Phase 1 Implementation

**Next Step:** Create `FileTree.tsx` component with recursive folder structure and integrate with existing tools.
