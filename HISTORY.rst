=======
History
=======

0.1.0 (2017-08-24)
------------------

* First release on PyPI.

0.2.0 (2018-12-20)
------------------

* Integrated changes from community: added monitoring of ouitputs.
* Attempt at fixing issue with "state unknown" of the alarm. Unfurtunately unsuccesful.

0.3.1 (2019-02-13)
------------------

* improved robustness when connection disapears
* fixed issues with "status unknown" which caused blocking of the functionality in HA
- still existing issues with alarm status - to be fixed

0.3.2 (2019-02-18)
------------------

* Fixed status issues
* Introduced "pending status"

0.3.3 (2019-03-07)
------------------

* Added ENTRY_TIME status to display "DISARMING" status in HA
* Fixed issue with unhandled connection error  causing HomeAssistant to give up on coommunication with eth module completely
