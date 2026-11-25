import sys


def main() -> None:
    argv = sys.argv[1:]
    if '--smoke' in argv:
        i = argv.index('--smoke')
        from . import gui
        raise SystemExit(gui.smoke_test(argv[i + 1]))
    if '--cli' in argv:
        from . import cli
        idx = argv.index('--cli')
        cli.main(argv[:idx] + argv[idx + 1:])
        return
    from . import gui
    gui.main()


if __name__ == '__main__':
    main()
