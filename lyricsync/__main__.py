import sys

from .util import setup_env


def main():
    setup_env()
    if len(sys.argv) > 1:
        from .cli import main as cli

        sys.exit(cli())
    from .gui import run

    run()


if __name__ == "__main__":
    main()
