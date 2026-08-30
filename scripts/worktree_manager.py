import os
import shutil
import subprocess

def create_worktree(repo_path: str, task_id: str, base_branch: str = "main") -> str:
    branch_name = f"feature/{task_id}"
    worktrees_dir = os.path.join(repo_path, "worktrees")
    os.makedirs(worktrees_dir, exist_ok=True)
    worktree_path = os.path.join(worktrees_dir, task_id)
    
    # Prune and force remove if it already exists
    subprocess.run(["git", "-C", repo_path, "worktree", "prune"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)
        
    cmd = ["git", "-C", repo_path, "worktree", "add", "-b", branch_name, worktree_path, base_branch]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        if "already exists" in res.stderr or "already used by worktree" in res.stderr:
            cmd = ["git", "-C", repo_path, "worktree", "add", worktree_path, branch_name]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                raise Exception(f"Failed to create worktree: {res.stderr}")
        else:
            raise Exception(f"Failed to create worktree: {res.stderr}")
            
    return worktree_path

def cleanup_worktree(repo_path: str, worktree_path: str) -> None:
    subprocess.run(["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", repo_path, "worktree", "prune"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)
