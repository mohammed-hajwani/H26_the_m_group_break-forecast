"""tinyfmt.

Usage:
  tinyfmt title <text>
  tinyfmt utc <when> <zone>
  tinyfmt (-h | --help)
"""

from docopt import docopt

from .dates import to_utc
from .text import title_words


def run(argv):
    args = docopt(__doc__, argv=argv)
    if args["title"]:
        return title_words(args["<text>"])
    return to_utc(args["<when>"], args["<zone>"])


def main(argv=None):
    print(run(argv))
