# Starter Code Review

| # | Where | What is wrong | How you'd notice it | How I fixed it |
|---|---|---|---|---|
| 1 | compute_late_minutes | Uses the punch timestamp's timezone instead of the IST shift date. | A UTC punch gives incorrect lateness. | Build shift start from the attendance date in IST. |
| 2 | compute_late_minutes | Floors minutes before checking grace. | 09:40:01 for a 09:30 shift incorrectly returns 0. | Compare elapsed seconds against 600 before flooring minutes. |
| 3 | compute_work_hours | Uses float rounding instead of half-up. | A duration of 1.005 hours rounds incorrectly. | Use Decimal and ROUND_HALF_UP. |
| 4 | compute_overtime | Counts overtime below 30 minutes. | Five minutes after shift end returns 5 instead of 0. | Apply the 30-minute minimum. |
| 5 | compute_overtime | Does not advance overnight shift end to the next day. | A 22:00–06:00 shift produces excessive overtime. | Add one day for overnight shifts. |
| 6 | EmployeeIn | Missing field and date validation. | Invalid employee codes, emails, dates, or shift times are accepted. | Add constraints and date validation; reject equal shift times. |
| 7 | PunchInIn | Accepts invalid statuses and timestamps. | LEAVE, strings, floats, or seconds-looking timestamps are accepted. | Restrict statuses and validate integer epoch milliseconds. |
| 8 | health | Does not check MongoDB readiness. | Returns 200 when MongoDB is unavailable. | Ping MongoDB and return 503 on failure. |
| 9 | create_employee | Check-then-insert does not prevent concurrent duplicates. | Two simultaneous creates can both succeed. | Create a unique emp_code index and handle DuplicateKeyError as 409. |
| 10 | create_employee | Uses naive local creation time and returns a datetime. | created_at is not integer epoch milliseconds. | Store UTC time and serialize it as epoch milliseconds. |
| 11 | list_employees | Uses page * page_size as the offset. | Page 1 skips the first 20 employees. | Use (page - 1) * page_size. |
| 12 | list_employees | Total ignores the department filter. | A department list reports the whole collection's total. | Count employees using the same query filter. |
| 13 | List endpoints | Missing pagination validation. | page=0 or page_size=101 is accepted. | Enforce page >= 1 and page_size between 1 and 100. |
| 14 | list_employees | Missing deterministic employee ordering. | Page contents depend on insertion order. | Sort by emp_code ascending. |
| 15 | punch_in | Does not handle an unknown employee. | Unknown emp_code causes a server error. | Return 404 before accessing employee fields. |
| 16 | punch_in | Uses local naive timestamps and the wrong attendance date. | UTC/IST boundaries and overnight punches are assigned incorrectly. | Convert to UTC, truncate seconds, and calculate the IST attendance date. |
| 17 | punch_in | Check-then-insert permits duplicate daily attendance. | Concurrent requests create duplicate records. | Add a unique (emp_code, date) index and return 409 for duplicate inserts. |
| 18 | Attendance responses | Returns an id and datetime values. | Response differs from the contract's natural keys and epoch milliseconds. | Remove internal IDs and serialize timestamps, including nested history. |
| 19 | list_attendance | Loads and sorts all matching records in Python. | Memory use and latency grow with the dataset. | Perform indexed sorting, pagination, and counting in MongoDB. |
| 20 | list_attendance | Missing date-range and status validation. | Reversed ranges or invalid statuses are accepted. | Validate dates, range order, and allowed statuses. |
| 21 | list_attendance | Missing employee-code tie ordering. | Records sharing a date have unstable order. | Sort by date descending, then emp_code ascending. |
| 22 | Startup | Does not create required indexes. | Uniqueness is unprotected and queries can scan collections. | Create unique and query indexes during application startup. |

## Checked and retained

- The environment variable names match the contract.
- load_dotenv() preserves existing environment variables by default.
- Attendance listing applies inclusive date bounds.