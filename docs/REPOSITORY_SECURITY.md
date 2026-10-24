# Repository Security

## Trust Model

Repository paths, files, commit messages, authors, and diffs are untrusted data. They never become system instructions. M2 exposes no shell Tool, Git write command, automatic submodule registration, network operation, or repository mutation.

Registered repository paths are an explicit administrator decision. Because container deployments commonly read admin-approved bind mounts owned by another UID, the bounded Git runner passes `-c safe.directory=*` so Git's ownership check cannot block an already registered read-only path. The command allowlist, path containment, and content policy remain the security boundary; Git still runs only fixed read-only subcommands without a shell.

## Path Policy

Public operations use typed POSIX-relative `RepositoryPath` values. Absolute, drive, UNC, NUL, backslash, traversal, and `.git` paths are rejected. Filesystem aliases are strictly resolved during registration. Commit tree reads do not follow filesystem symlinks or junctions. Git symlink blobs and submodule entries are reported distinctly and cannot be read as files.

## Content Policy

The provider excludes `.env*`, credential filenames, private-key extensions/headers, common credential assignments, dependency/build directories, archives, media, executables, databases, and binary extensions. Binary checks combine SHA-selected Git attributes, NUL detection, extension denylist, control-byte sampling, and strict UTF-8/BOM decoding. System/global attribute files are disabled; repository-local metadata can only contribute a conservative rejection and cannot bypass the independent content checks.

This is a basic leak-prevention layer, not complete DLP. False positives and false negatives remain possible. Rejected content is represented only by safe errors or aggregate warnings; content and Git stderr are not public messages or logs.

## Limits

Defaults are 2 GiB per repository, 1 MiB per text/search file, 1,000 directory entries, 100 search hits, 2 MiB diff output, 4 MiB command streams, and 30-second commands. Settings impose hard upper bounds.

## Search Snapshot

Only eligible commit blobs enter an OS temporary directory with an unpredictable name and owner-restricted permissions where supported. No `.git` metadata, symlink, submodule, secret candidate, binary, or working-tree change is copied. Unicode-normalized and case-insensitive path collisions are rejected before an existing snapshot file can be overwritten. ripgrep and the fixed-string fallback operate on the same snapshot, which is cleaned on every exit path. Cleanup failures become safe repository errors without exposing the temporary absolute path.

ripgrep is the only M2 regex engine. When ripgrep is unavailable, fixed-string and case-sensitive/insensitive search remain available through the standard-library fallback, but regex search fails safely. This avoids running an unbounded backtracking regular expression in the application event loop and avoids claiming incompatible regex semantics.

## Residual Risks

Path/content rules are intentionally conservative. Repository-size calculation can be expensive for very large local repositories. PID/cancellation and process-tree behavior can vary by OS; M2 disables known child-process paths but does not yet use a Windows Job Object. Windows directory symlink tests require developer-mode or elevated privileges; Git object symlink rejection remains covered without that privilege.
