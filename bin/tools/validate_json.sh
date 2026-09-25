# Source - https://stackoverflow.com/a/76498905
# Posted by peak, modified by community. See post 'Timeline' for change history
# Retrieved 2026-09-24, License - CC BY-SA 4.0

(cat 1.json 2.json 1.json1) | jq -n '

# If cond is falsey, then (msg|debug); in all cases, emit .
def verify(cond; msg): 
  . as $in
  | if cond then . else (msg | debug) | $in end;

def valid_key: test("^[A-Za-z_][0-9A-Za-z_]*$");

def valid_value:
  type != "array" and 
  type != "object" and
  (type != "string" or (test("\u0000") | not) );

# $count is for the messages and for the return value
def validate($count):
   if type == "object"
   then to_entries[]
   | verify(.key | valid_key; "invalid key in entity #\($count): \(.key)")
   | verify(.value | valid_value; "invalid value in entity #\($count): \(.value)")
   else "entity #\($count) is not a JSON object" | debug
   end
   | $count;

reduce inputs as $in (0;
  .+1
  | . as $count
  | $in | validate($count) )
| verify(.==1; "only one entity is allowed")
| empty
'

