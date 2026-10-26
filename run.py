"""DM Recipe Backup/Parsing/Aggregate entry point."""

from __future__ import annotations

import warnings

from src import create_collector, create_recipe_parser

warnings.simplefilter(action="ignore", category=FutureWarning)

collector = create_collector()
recipe_parser = create_recipe_parser()


def main():
    """Main 프로세스"""
    collector.run()
    recipe_parser.run()


if __name__ == "__main__":
    main()
