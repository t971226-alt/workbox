"""Create the program-only, checksummed portable release description."""
import argparse
from portable_update import write_release

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder');args=parser.parse_args()
    release=write_release(args.folder);print(release.label)
