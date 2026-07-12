# Repository Security

## Trust Model

Repository paths, files, commit messages, authors, and diffs are untrusted data. They never become system instructions. M2 exposes no shell Tool, Git write command, automatic submodule registration, network operation, or repository mutation.

## Path Policy

Public operations use typed POSIX-relative `RepositoryPath` values. Absolute, drive, UNC, NUL, backslash, traversal, and `.git` paths are rejected. Filesystem aliases are strictly resolved during registration. Commit tree reads do not follow filesystem symlinks or junctions. Git symlink blobs and submodule entries are reported distinctly and cannot be read as files.

## Content Policy

The provider excludes `.env*`, credential filenames, private-key extensions/headers, common credential assignments, dependency/build directories, archives, media, executables, databases, and binary extensions. Binary checks combine commit-pinned Git attributes, NUL detection, extension denylist, control-byte sampling, and strict UTF-8/BOM decoding.

This is a basic leak-prevention layer, not complete DLP. False positives and false negatives remain possible. Rejected content is represented only by safe errors or aggregate warnings; content and Git stderr are not public messages or logs.

## Limits

Defaults are 2 GiB per repository, 1 MiB per text/search file, 1,000 directory entries, 100 search hits, 2 MiB diff output, 4 MiB command streams, and 30-second commands. Settings impose hard upper bounds.

## Search Snapshot

Only eligible commit blobs enter an OS temporary directory with an unpredictable name. No `.git` metadata, symlink, submodule, secret candidate, binary, or working-tree change is copied. ripgrep and fallback operate on the same snapshot, which is cleaned on every exit path.

## Residual Risks

Path/content rules are intentionally conservative. Repository-size calculation can be expensive for very large local repositories. PID/cancellation and process-tree behavior can vary by OS. Windows directory symlink tests require developer-mode or elevated privileges; Git object symlink rejection remains covered without that privilege.
