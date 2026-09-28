#!env bash
# Check syntax of patterns file for egrep (grep -E) using an empty input string.
if echo -n "" | grep -E -f patterns.txt > /dev/null 2>&1; [ $? -eq 2 ]; then
    echo "Syntax Error detected in patterns file!"
else
    echo "Regex syntax is valid."
fi

