Set-Location 'C:\Users\OLUWASEYI\jarvis_project'
$changes = git status --porcelain
if ($changes) {
    git add -A
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
    git commit -m "Auto-sync: $timestamp"
    git push origin main
}
