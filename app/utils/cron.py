from __future__ import annotations

from datetime import datetime, timedelta


class CronExpression:
    def __init__(self, expression: str):
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("scheduler_cron must contain exactly 5 fields")

        self._expression = expression
        self._minute_values, self._minute_wildcard = self._parse_field(fields[0], 0, 59)
        self._hour_values, self._hour_wildcard = self._parse_field(fields[1], 0, 23)
        self._day_values, self._day_wildcard = self._parse_field(fields[2], 1, 31)
        self._month_values, self._month_wildcard = self._parse_field(fields[3], 1, 12)
        self._weekday_values, self._weekday_wildcard = self._parse_field(
            fields[4],
            0,
            7,
            normalize_weekday=True,
        )

    @property
    def expression(self) -> str:
        return self._expression

    def next_after(self, reference: datetime) -> datetime:
        if reference.tzinfo is None:
            raise ValueError("reference datetime must be timezone-aware")

        candidate = reference.replace(second=0, microsecond=0) + timedelta(minutes=1)
        max_attempts = 366 * 24 * 60

        for _ in range(max_attempts):
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)

        raise RuntimeError(f"Unable to resolve next run for cron expression: {self._expression}")

    def matches(self, candidate: datetime) -> bool:
        cron_weekday = (candidate.weekday() + 1) % 7
        return (
            candidate.minute in self._minute_values
            and candidate.hour in self._hour_values
            and candidate.month in self._month_values
            and self._matches_day(candidate.day, cron_weekday)
        )

    def _matches_day(self, day_of_month: int, day_of_week: int) -> bool:
        day_matches = day_of_month in self._day_values
        weekday_matches = day_of_week in self._weekday_values

        if self._day_wildcard and self._weekday_wildcard:
            return True
        if self._day_wildcard:
            return weekday_matches
        if self._weekday_wildcard:
            return day_matches
        return day_matches or weekday_matches

    @staticmethod
    def _parse_field(
        expression: str,
        minimum: int,
        maximum: int,
        *,
        normalize_weekday: bool = False,
    ) -> tuple[set[int], bool]:
        is_wildcard = expression.strip() == "*"
        values: set[int] = set()

        for part in expression.split(","):
            part = part.strip()
            if not part:
                raise ValueError(f"Invalid empty cron field segment in '{expression}'")

            step = 1
            base = part
            if "/" in part:
                base, raw_step = part.split("/", 1)
                try:
                    step = int(raw_step)
                except ValueError as exc:
                    raise ValueError(f"Invalid cron step '{raw_step}' in '{expression}'") from exc
                if step <= 0:
                    raise ValueError(f"Cron step must be positive in '{expression}'")

            if base == "*":
                start = minimum
                end = maximum
            elif "-" in base:
                raw_start, raw_end = base.split("-", 1)
                start = CronExpression._parse_value(
                    raw_start,
                    minimum,
                    maximum,
                    expression=expression,
                )
                end = CronExpression._parse_value(
                    raw_end,
                    minimum,
                    maximum,
                    expression=expression,
                )
                if end < start:
                    raise ValueError(f"Cron range must be ascending in '{expression}'")
            else:
                start = CronExpression._parse_value(
                    base,
                    minimum,
                    maximum,
                    expression=expression,
                )
                end = start

            for value in range(start, end + 1, step):
                values.add(0 if normalize_weekday and value == 7 else value)

        return values, is_wildcard

    @staticmethod
    def _parse_value(
        raw_value: str,
        minimum: int,
        maximum: int,
        *,
        expression: str,
    ) -> int:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"Invalid cron value '{raw_value}' in '{expression}'") from exc

        if value < minimum or value > maximum:
            raise ValueError(
                f"Cron value '{raw_value}' is outside {minimum}-{maximum} in '{expression}'"
            )

        return value
