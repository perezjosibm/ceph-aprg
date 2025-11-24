#!/bin/sh
#
# Take a core dump and create a tarball of all of the binaries and libraries
# that are needed to debug it.
#

include_core=1
keep_workdir=0

usage()
{
        argv0="$1"
        retval="$2"
        errmsg="$3"
        if [ ! -z "$errmsg" ] ; then
                echo "ERROR: $errmsg" 1>&2
        fi
        cat <<EOF
Usage: $argv0 [-k] [-x] <corefile>
        Parse a core dump and create a tarball with all binaries and libraries
        needed to be able to debug the core dump.
        Creates <corefile>.tgz

        -k - Keep temporary working directory
        -x - Exclude the core dump from the generated tarball
EOF
        exit $retval
}

while [ $# -gt 0 ] ; do
        case "$1" in
        -k)
                keep_workdir=1
                ;;
        -x)
                include_core=0
                ;;
        -h|--help)
                usage "$0" 0
                ;;
        -*)
                usage "$0" 1 "Unknown command line arguments: $*"
                ;;
        *)
                break
                ;;
        esac
        shift
done

COREFILE="$1"

if [ ! -e "$COREFILE" ] ; then
        usage "$0" 1 "core dump '$COREFILE' doesn't exist."
fi
case "$(file "$COREFILE")" in
        *"core file"*)
                break
                ;;
        *)
                usage "$0" 1 "per the 'file' command, core dump '$COREFILE' is not a core dump."
                ;;
esac

cmdname=$(file "$COREFILE" | sed -e"s/.*from '\(.*\)'/\1/")
echo "Command name from core file: $cmdname"
fullpath=$(which "$cmdname")
if [ ! -x "$fullpath" ] ; then
        usage "$0" 1 "unable to find command '$cmdname'"
fi
echo "Full path to executable: $fullpath"

mkdir "${COREFILE}.pack"
gdb --eval-command="quit" "${fullpath}" ${COREFILE}  2>&1 | \
  grep "Reading symbols" | \
  sed -e's/Reading symbols from //' -e's/\.\.\..*//' | \
  tar --files-from=- -cf - | (cd "${COREFILE}.pack" && tar xf -)
if [ $include_core -eq 1 ] ; then
        cp "${COREFILE}" "${COREFILE}.pack"
fi
tar czf "${COREFILE}.pack.tgz" "${COREFILE}.pack"

if [ $keep_workdir -eq 0 ] ; then
        rm -r "${COREFILE}.pack"
fi

echo "Done, created ${COREFILE}.path.tgz"


