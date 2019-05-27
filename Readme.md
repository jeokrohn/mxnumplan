# Numbering plan change in Mexico in August 2019

Mexico in August 2019 will change their national numbering plan to a closed fixed length (10 digit) numbering
plan:  https://www.itu.int/dms_pub/itu-t/oth/02/02/T020200008A0003PDFE.pdf

While previously cellphone numbers were easily identifiable by their common prefix in the new numbering plan
cellphone  numbers share the same prefix(es) as the other geographical numbers. Enterprise admins still want to be
able  to determine which number are cellphone numbers to be able to implement differentiated class of service (with
or without access to cellphone numbers).

One way to achieve that is to provision blocking translation patterns in UCM to block access to mobile cellphone
number ranges. This partition with blocking patterns can then be used to built a "no cellphone" class of service.
The question remains: What are the cellphone number ranges?

To answer that question the Mexcian numbering plan authority publishes a CSV file with all number ranges in Mexico
at:  https://sns.ift.org.mx:8081/sns-frontend/planes-numeracion/descarga-publica.xhtml. This list is continuously
updated.

This Python script:

* pulls the latest numbering plan from the website
* identifies the mobile ranges
* summarizes these ranges to a minimal set of patterns
* provisions blocking translation patterns all of these patterns

The script requires Python 3.6 or later.    

# Usage

```
mxnumplan.py [-h] [--ucm UCM] [--user USER] [--pwd PWD]
                  [--fromfile FROMFILE] [--readonly] [--analysis]

Provision blocking translation patterns to cover all mobile phone number in
Mexico. The blocking translation patterns are put into a 'blockmobile'
partition which is also created if it doesn't exist.

optional arguments:
  -h, --help           show this help message and exit
  --ucm UCM            IP or FQDN of UCM publisher host. If ucm is not given
                       then only the patterns are printed
  --user USER          AXL user with write access to UCM
  --pwd PWD            Password for AXL user with write access to UCM
  --fromfile FROMFILE  name of ZIP file to read patterns from. If the file
                       name is given as "." then we take the latest
                       pnn_Publico_??_??_????.zip
  --readonly           Don't write to UCM. Existing patterns are read if
                       possible.
  --analysis           If present, then compare patterns of existing data
                       sets stored in pnn_Publico_??_??_????.zip

```