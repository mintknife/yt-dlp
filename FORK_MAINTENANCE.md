# Fork Maintenance Guide

This fork contains a fix for the CAM4 extractor that improves error handling for offline/not-streaming performers.

## Initial Setup (one time only)

```bash
git remote add upstream https://github.com/yt-dlp/yt-dlp.git
```

## Updating the Fork

When yt-dlp releases a new version and you want to update:

```bash
# Fetch updates from upstream
git fetch upstream

# Rebase your changes on top of upstream
git rebase upstream/master

# Push to your fork (force required after rebase)
git push origin master --force
```

## Handling Conflicts

If there are conflicts on `yt_dlp/extractor/cam4.py`:

1. Open the file and look for conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. Manually merge keeping your improvements + any upstream changes
3. Save the file, then:

```bash
git add yt_dlp/extractor/cam4.py
git rebase --continue
git push origin master --force
```

## Installing in Your Project

```bash
pip install git+https://github.com/mintknife/yt-dlp.git
```

Or in `requirements.txt`:
```
git+https://github.com/mintknife/yt-dlp.git
```

## Changes in This Fork

### CAM4 Extractor (`yt_dlp/extractor/cam4.py`)

- Added check for performer online status via `/rest/v1.0/profile/{username}/info`
- Clear error messages:
  - "Performer not found" (404)
  - "Performer is currently offline" (online: false)
  - "Performer is online but not currently streaming" (204 on streamInfo)
