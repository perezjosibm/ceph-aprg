#!/bin/bash

declare -A PERSONS
declare -A PERSON

PERSON["FNAME"]='John'
PERSON["LNAME"]='Andrew'
string=$(declare -p PERSON)
#printf "${string}\n"
PERSONS["1"]=${string}
#echo ${PERSONS["1"]}

PERSON["FNAME"]='Elen'
PERSON["LNAME"]='Murray'
string=$(declare -p PERSON)
#printf "${string}\n"
PERSONS["2"]=${string}
#echo ${PERSONS["2"]}

for KEY in "${!PERSONS[@]}"; do
   printf "$KEY - ${PERSONS["$KEY"]}\n"
   eval "${PERSONS["$KEY"]}"
   printf "${PERSONS["$KEY"]}\n"
   for KEY in "${!PERSON[@]}"; do
      printf "INSIDE $KEY - ${PERSON["$KEY"]}\n"
   done
done

