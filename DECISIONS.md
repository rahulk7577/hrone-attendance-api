# Design Decisions

1. **Indexes.** Employees have a unique emp_code index. Attendance logs have a unique (emp_code, date) index, preventing duplicate daily records. Date/employee and status/date/employee indexes support attendance lists and date filtering. An employee/punch-in index supports finding the latest punch-in. Employee joining-date and department indexes support summaries and trends. I rejected a work_hours index because queries filter by employee, date, or department.

2. **Punch-in race.** Both requests may find no existing record and attempt insertion. The unique employee/date index allows one insert. The other raises DuplicateKeyError, which the API handles with 409. The successful request returns 201.

3. **Ties.** The leaderboard ranks employees by total late minutes and returns ranks up to limit. Everyone tied at the cutoff is included, so results may exceed limit. Employee code provides a stable display order within ties.

4. **Headcount.** The summary starts from employees who joined by month-end, then looks up attendance. Preserving employees with no matching logs ensures they count toward headcount.

5. **At 100x the data.** I would measure query performance and consider precomputed daily department totals to reduce repeated aggregation in trend reports.