# File Tree Implementation Decision

**Date:** 2026-08-24  
**Decision Log:** #19 (proposed)  
**Status:** Ready for implementation review  

---

## 1. Decision Overview

### 1.1 What We're Building
Replacing the **GitHub repo dropdown + file path input** pattern with a **VS Code-like file explorer tree** that:
- Lists files and folders from the selected GitHub repository
- Shows nested directory structure recursively
- Allows navigation to any file in the repo
- Integrates with existing `RepoListFiles` backend tool

### 1.2 Why Replace Dropdown + Input?
| Current Pattern | File Tree Pattern | Benefit |
|-----------------|-------------------|---------|
| User selects repo from dropdown | Repo selected once, files auto-loaded | No repeated selection needed |
| User manually types file path | Click to navigate folders/files | Less error-prone, more intuitive |
| Requires typing `src/app.py` | Browse tree structure naturally | Better UX for large repos |
| No visual feedback on repo state | Shows file count, sync status | Immediate awareness of changes |

---

## 2. Key Facts & Constraints

### 2.1 Technical Dependencies
| Component | Purpose | Status |
|-----------|---------|--------|
| `RepoListFiles` tool | Backend endpoint to list files | ✅ Exists (`backend/app/agents/tools.py`) |
| `EditorPane` component | Current file input mechanism | ⚠️ To be replaced |
| `gitops` module | Git operations (clone, list paths) | ✅ Exists |
| Monaco Editor | Code editing with syntax highlighting | ✅ Already integrated |

### 2.2 File Path Format
- **Repo-relative** (e.g., `src/app.py`, `docs/MASTER_SPEC.md`)
- **Root path**: `/` or empty string represents repo root
- **File extensions**: Visible in tree for quick identification
- **Skipped files**: `.git`, `node_modules`, `dist/`, `__pycache__`, lockfiles (per spec §9)

### 2.3 Backend Integration Points
```python
# backend/app/agents/tools.py - RepoListFiles tool
async def repo_list_files(repo_id: str, path: str = "/") -> list[dict]:
    """List files in a repository at given path."""
    # Uses gitops.list_file_paths() internally
    # Returns: [{name, type, children}, ...]
```

### 2.4 Frontend Integration Points
| File | Role | Status |
|------|------|--------|
| `frontend/src/components/EditorPane.tsx` | Current file picker (to be replaced) | ⚠️ Modify |
| `frontend/src/components/FileTree.tsx` | New file tree component | ✅ To create |
| `frontend/src/main.tsx` | App entry point | ✅ Update |
| `frontend/src/lib/gitops.ts` | Git operations utilities | ✅ Exists |

---

## 3. Implementation Plan

### 3.1 Phase 1: File Tree Component (`FileTree.tsx`)
```typescript
// frontend/src/components/FileTree.tsx
import { useState, useEffect } from 'react';
import { RepoListFiles } from '../lib/tools';

export function FileTree({ repoId, onFileSelect }) {
  const [files, setFiles] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  
  // Load files from backend
  useEffect(() => {
    loadFiles(repoId, "/");
  }, [repoId]);

  async function loadFiles(path: string) {
    const result = await RepoListFiles({ repoId, path });
    setFiles(result);
  }

  return (
    <div className="file-explorer">
      {/* Recursive folder structure */}
    </div>
  );
}
```

### 3.2 Phase 2: Replace Dropdown in `EditorPane.tsx`
| Change | Details |
|--------|---------|
| Remove `<select>` for repo selection | Keep repo dropdown (users may switch repos) |
| Add file tree sidebar | New component slot |
| Update file path input | Auto-filled from selected file, editable |
| Add refresh button | Reload files after git sync |

### 3.3 Phase 3: Integration with Monaco Editor
```typescript
// When user selects a file in the tree:
onFileSelect(filePath) => {
  // Set Monaco model content
  editor.setModelContent(model, filePath);
  // Update EditorPane state
  setFilePath(filePath);
}
```

---

## 4. Key Considerations

### 4.1 Performance
- **File listing**: Backend handles git operations (asynchronous)
- **Rendering**: React virtualization for deep folder trees
- **Sync status**: Show "synced" indicator when files match latest commit

### 4.2 UX Details
| Feature | Implementation |
|---------|----------------|
| Expand/collapse folders | Click or arrow icon toggle |
| File selection | Click to highlight, double-click to open |
| Keyboard shortcuts | `↑/↓` navigate, `Enter` open, `Esc` close |
| Search | Sidebar search over file names (optional enhancement) |
| Loading state | Spinner while fetching files from backend |

### 4.3 Error Handling
- **Repo not found**: Show error message
- **File not found**: Grey out or show warning
- **Sync in progress**: Disable selection until complete
- **Large repos**: Implement pagination/virtualization

---

## 5. References

| Document | Purpose |
|----------|---------|
| `docs/MASTER_SPEC.md` | Original requirements & constraints |
| `backend/app/agents/tools.py` | RepoListFiles tool implementation |
| `frontend/src/components/EditorPane.tsx` | Current file picker (to modify) |
| `frontend/src/lib/gitops.ts` | Git operations utilities |

---

## 6. Next Steps

1. **Review this decision** with team
2. **Create `FileTree.tsx` component** (Phase 1)
3. **Update `EditorPane.tsx`** to integrate tree (Phase 2)
4. **Test with large repos** for performance (Phase 3)
5. **Add keyboard shortcuts** and polish UX

---

*This decision is based on the existing backend tools (`RepoListFiles`) and frontend architecture. No new API endpoints are required.*
