"""Opt-in daily scan. Transcripts are pruned from disk, so unscanned usage is lost."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = 'com.claude-usage.scan'


def plist_path():
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'


def build_plist(python=sys.executable, cli=None, hour=12, minute=30):
    cli = Path(cli) if cli else Path(__file__).with_name('cli.py')
    log = str(Path.home() / '.claude' / 'usage-scan.log')
    return {
        'Label': LABEL,
        'ProgramArguments': [python, str(cli.resolve()), 'scan'],
        'WorkingDirectory': str(cli.resolve().parent),
        # launchd runs a missed calendar job when the machine next wakes.
        'StartCalendarInterval': {'Hour': hour, 'Minute': minute},
        'StandardOutPath': log,
        'StandardErrorPath': log,
    }


def install():
    if sys.platform != 'darwin':
        cli = Path(__file__).with_name('cli.py').resolve()
        print('Automatic install is only implemented for macOS. Add this line to your crontab:')
        print(f'30 12 * * * {sys.executable} {cli} scan')
        return 1
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        plistlib.dump(build_plist(), stream)
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', domain, str(path)], capture_output=True)
    done = subprocess.run(['launchctl', 'bootstrap', domain, str(path)], capture_output=True, text=True)
    if done.returncode:
        print(f'Wrote {path} but launchctl could not load it: {done.stderr.strip()}')
        return 1
    print(f'Installed a daily scan (12:30) at {path}')
    print(f'Log: {Path.home() / ".claude" / "usage-scan.log"}')
    print(f'Remove with: launchctl bootout {domain} {path} && rm {path}')
    return 0
