#!/usr/bin/env python3
"""Create v1 directories only; never replace files or follow layout symlinks."""
import argparse
from pathlib import Path

DIRECTORIES = (
    '00 Inbox', '00 Inbox/Clips', 'raw', 'wiki', 'outputs',
    '90 System', '90 System/attachments', '90 System/wiki-templates',
    '90 System/scripts', '90 System/tests', '90 System/tasks',
    *(f'raw/{name}' for name in ('university', 'papers', 'web', 'books', 'career', 'research', 'personal', 'projects')),
    *(f'wiki/{name}' for name in ('courses', 'concepts', 'entities', 'methods', 'projects', 'decisions', 'syntheses', 'sources')),
    *(f'outputs/{name}' for name in ('presentations', 'career', 'research')),
)


def initialize(root: Path) -> list[str]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('root must be an existing directory')
    # Preflight the whole layout before creating anything.
    for relative in DIRECTORIES:
        path = root
        for part in Path(relative).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError(f'layout symlink is not allowed: {relative}')
            if path.exists() and not path.is_dir():
                raise ValueError(f'file occupies directory path: {relative}')
    created = []
    for relative in DIRECTORIES:
        path = root / relative
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(relative)
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    try:
        created = initialize(args.root)
    except (OSError, ValueError) as error:
        parser.exit(2, f'init failed: {error}\n')
    print(f'Created {len(created)} directories; existing content unchanged.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
