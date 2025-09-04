#!/bin/bash
. <( sed -n '/^#SOURCE-BEGIN/,/^#SOURCE-END/{//!p;}' $0 )
greeting "$@"
foo hey

#SOURCE-BEGIN
greeting() {
  for i in "$@"
  do
    echo ">[$i]"
  done
}

foo() {
  echo in foo
  echo "arg passed in: $1"
}
#SOURCE-END

echo good bye

