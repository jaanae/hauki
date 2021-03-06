import datetime
from calendar import Calendar
from collections import defaultdict, namedtuple
from itertools import chain
from typing import List, Set, Union

from dateutil.relativedelta import SU, relativedelta
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_orghierarchy.models import Organization
from enumfields import EnumField, EnumIntegerField
from model_utils.models import SoftDeletableModel, TimeStampedModel

from hours.enums import (
    FrequencyModifier,
    ResourceType,
    RuleContext,
    RuleSubject,
    State,
    Weekday,
)

TimeElement = namedtuple(
    "TimeElement",
    ["start_time", "end_time", "resource_state", "override", "full_day"],
)


def get_range_overlap(start1, end1, start2, end2):
    min_end = min(end1, end2) if end1 and end2 else end1 or end2
    max_start = max(start1, start2) if start1 and start2 else start1 or start2

    return (
        max_start if min_end >= max_start else None,
        min_end if max_start <= min_end else None,
    )


def expand_range(start_date, end_date):
    if end_date < start_date:
        raise ValueError("Start must be before end")

    date_delta = end_date - start_date

    dates = []
    for i in range(date_delta.days + 1):
        dates.append(start_date + datetime.timedelta(days=i))

    return dates


class DataSource(SoftDeletableModel, TimeStampedModel):
    id = models.CharField(max_length=100, primary_key=True)
    name = models.CharField(verbose_name=_("Name"), max_length=255)
    description = models.TextField(verbose_name=_("Description"), null=True, blank=True)
    user_editable = models.BooleanField(
        default=False, verbose_name=_("Objects may be edited by users")
    )

    class Meta:
        verbose_name = _("Data source")
        verbose_name_plural = _("Data sources")

    def __str__(self):
        return self.id


class Resource(SoftDeletableModel, TimeStampedModel):
    name = models.CharField(
        verbose_name=_("Name"), max_length=255, null=True, blank=True
    )
    description = models.TextField(verbose_name=_("Description"), null=True, blank=True)
    address = models.TextField(verbose_name=_("Street address"), null=True, blank=True)
    resource_type = EnumField(
        ResourceType,
        verbose_name=_("Resource type"),
        max_length=100,
        default=ResourceType.UNIT,
    )
    children = models.ManyToManyField(
        "self",
        verbose_name=_("Sub resources"),
        related_name="parents",
        blank=True,
        symmetrical=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="resources",
        db_index=True,
        null=True,
        blank=True,
    )
    data_sources = models.ManyToManyField(DataSource, through="ResourceOrigin")
    last_modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
    )
    extra_data = models.JSONField(verbose_name=_("Extra data"), null=True, blank=True)
    is_public = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Resource")
        verbose_name_plural = _("Resources")

    def __str__(self):
        return str(self.name)

    @property
    def _history_user(self):
        return self.last_modified_by

    @_history_user.setter
    def _history_user(self, value):
        self.last_modified_by = value

    def get_daily_opening_hours(self, start_date, end_date):
        periods = self.date_periods.filter(
            Q(
                Q(Q(end_date=None) | Q(end_date__gte=start_date))
                & Q(Q(start_date=None) | Q(start_date__lte=end_date))
            )
        )

        # TODO: This is just an MVP. Things yet to do:
        #       - Combine multiple times of same types
        #       - Handle multiple types in the same time period
        #       - Handle override
        #       - Support full_day

        all_daily_opening_hours = defaultdict(list)
        for period in periods:
            period_daily_opening_hours = period.get_daily_opening_hours(
                start_date, end_date
            )
            for the_date, time_items in period_daily_opening_hours.items():
                all_daily_opening_hours[the_date].extend(time_items)

        return all_daily_opening_hours


class ResourceOrigin(models.Model):
    resource = models.ForeignKey(
        Resource, related_name="origins", on_delete=models.CASCADE
    )
    data_source = models.ForeignKey(DataSource, on_delete=models.CASCADE)
    origin_id = models.CharField(
        verbose_name=_("Origin ID"), max_length=100, db_index=True
    )

    class Meta:
        verbose_name = _("Resource origin")
        verbose_name_plural = _("Resource origins")
        constraints = [
            models.UniqueConstraint(
                fields=["data_source", "origin_id"],
                name="unique_identifier_per_data_source",
            ),
        ]

    def __str__(self):
        return f"{self.data_source}:{self.origin_id}"


class DatePeriod(SoftDeletableModel, TimeStampedModel):
    resource = models.ForeignKey(
        Resource, on_delete=models.PROTECT, related_name="date_periods", db_index=True
    )
    name = models.CharField(
        verbose_name=_("Name"), max_length=255, null=True, blank=True
    )
    description = models.TextField(verbose_name=_("Description"), null=True, blank=True)
    start_date = models.DateField(
        verbose_name=_("Start date"), null=True, blank=True, db_index=True
    )
    end_date = models.DateField(
        verbose_name=_("End date"), null=True, blank=True, db_index=True
    )
    resource_state = EnumField(
        State,
        verbose_name=_("Resource state"),
        max_length=100,
        default=State.UNDEFINED,
    )
    override = models.BooleanField(
        verbose_name=_("Override"), default=False, db_index=True
    )

    class Meta:
        verbose_name = _("Period")
        verbose_name_plural = _("Periods")

    def __str__(self):
        return f"{self.name}({self.start_date} - {self.end_date} {self.resource_state})"

    def get_daily_opening_hours(self, start_date, end_date):
        overlap = get_range_overlap(
            start_date, end_date, self.start_date, self.end_date
        )

        all_dates = set(expand_range(overlap[0], overlap[1]))
        result = defaultdict(list)

        for time_span_group in self.time_span_groups.all():
            rules = time_span_group.rules.all()
            time_spans = time_span_group.time_spans.all()

            if rules.count():
                for rule in rules:
                    matching_dates = rule.apply_to_date_range(overlap[0], overlap[1])
                    all_dates &= matching_dates

            for one_date in all_dates:
                for time_span in time_spans:
                    if (
                        not time_span.weekdays
                        or Weekday.from_iso_weekday(one_date.isoweekday())
                        in time_span.weekdays
                    ):
                        result[one_date].append(
                            TimeElement(
                                start_time=time_span.start_time,
                                end_time=time_span.end_time,
                                resource_state=self.resource_state,
                                override=self.override,
                                full_day=time_span.full_day,
                            )
                        )

        return result


class TimeSpanGroup(models.Model):
    period = models.ForeignKey(
        DatePeriod, on_delete=models.PROTECT, related_name="time_span_groups"
    )


class TimeSpan(SoftDeletableModel, TimeStampedModel):
    group = models.ForeignKey(
        TimeSpanGroup, on_delete=models.PROTECT, related_name="time_spans"
    )
    name = models.CharField(
        verbose_name=_("Name"), max_length=255, null=True, blank=True
    )
    description = models.TextField(verbose_name=_("Description"), null=True, blank=True)
    start_time = models.TimeField(
        verbose_name=_("Start time"), null=True, blank=True, db_index=True
    )
    end_time = models.TimeField(
        verbose_name=_("End time"), null=True, blank=True, db_index=True
    )
    full_day = models.BooleanField(verbose_name=_("24 hours"), default=False)
    weekdays = ArrayField(
        EnumIntegerField(
            Weekday,
            verbose_name=_("Weekday"),
            default=None,
        ),
        null=True,
        blank=True,
    )
    resource_state = EnumField(
        State,
        verbose_name=_("Resource state"),
        max_length=100,
        default=State.UNDEFINED,
    )

    class Meta:
        verbose_name = _("Time span")
        verbose_name_plural = _("Time spans")

    def __str__(self):
        weekdays = ", ".join([str(i) for i in self.weekdays])

        return f"{self.name}({self.start_time} - {self.end_time} {weekdays})"


class Rule(SoftDeletableModel, TimeStampedModel):
    group = models.ForeignKey(
        TimeSpanGroup, on_delete=models.PROTECT, related_name="rules"
    )
    name = models.CharField(
        verbose_name=_("Name"), max_length=255, null=True, blank=True
    )
    description = models.TextField(verbose_name=_("Description"), null=True, blank=True)
    context = EnumField(
        RuleContext,
        verbose_name=_("Context"),
        max_length=100,
    )
    subject = EnumField(
        RuleSubject,
        verbose_name=_("Subject"),
        max_length=100,
    )
    start = models.IntegerField(verbose_name=_("Start"), null=True, blank=True)
    frequency_ordinal = models.PositiveIntegerField(
        verbose_name=_("Frequency (ordinal)"), null=True, blank=True
    )
    frequency_modifier = EnumField(
        FrequencyModifier,
        verbose_name=_("Frequency (modifier)"),
        max_length=100,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("Rule")
        verbose_name_plural = _("Rules")

    def get_ordinal_for_item(
        self, item: Union[List[datetime.date], datetime.date]
    ) -> Union[None, int]:
        """Return ordinal for the provided context item"""
        if not item:
            return None

        # TODO: Is checking the first item sufficient?
        if isinstance(item, list):
            item = item[0]

        if self.subject.is_singular():
            return item.day
        if self.subject == RuleSubject.WEEK:
            return item.isocalendar()[1]
        if self.subject == RuleSubject.MONTH:
            return item.month

    def _filter_context_set(self, context_set: list) -> list:
        """Filter the provided context set by start and frequency"""
        if not self.frequency_modifier and not self.frequency_ordinal:
            if self.start is None:
                return context_set

            # TODO: When the context is YEAR and the subject is WEEK we should probably
            #       use the iso week number here
            try:
                return [context_set[self.start if self.start < 0 else self.start - 1]]
            except IndexError:
                return []

        if self.frequency_ordinal:
            if self.start is None:
                # TODO: Should we default to start=1?
                return context_set

            # TODO: When the context is YEAR and the subject is WEEK we should probably
            #       use the iso week number here
            try:
                return context_set[
                    self.start
                    if self.start < 0
                    else self.start - 1 :: self.frequency_ordinal
                ]
            except IndexError:
                return []
        elif self.frequency_modifier:
            result = []
            for item in context_set:
                num = self.get_ordinal_for_item(item)
                if self.frequency_modifier == FrequencyModifier.EVEN and num % 2 == 0:
                    result.append(item)
                if self.frequency_modifier == FrequencyModifier.ODD and num % 2 == 1:
                    result.append(item)

            return result

    def get_context_sets(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> List:
        """Get context sets defined by the Rules context and subject"""
        period_start_date = self.group.period.start_date
        period_end_date = self.group.period.end_date

        max_start_year = start_date.year
        if period_start_date:
            max_start_year = max(start_date.year, period_start_date.year)

        min_end_year = end_date.year
        if period_end_date:
            min_end_year = min(end_date.year, period_end_date.year)

        if self.context == RuleContext.PERIOD:
            if self.subject == RuleSubject.DAY:
                return [expand_range(period_start_date, period_end_date)]

            elif self.subject == RuleSubject.WEEK:
                week_start = period_start_date - relativedelta(
                    days=period_start_date.weekday()
                )
                week_end = week_start + relativedelta(weekday=SU(1))

                weeks = []
                while week_start <= period_end_date:
                    weeks.append(expand_range(week_start, week_end))
                    week_start = week_start + relativedelta(weeks=1)
                    week_end = week_start + relativedelta(weekday=SU(1))

                return [weeks]

            elif self.subject == RuleSubject.MONTH:
                first_day = datetime.date(
                    year=period_start_date.year,
                    month=period_start_date.month,
                    day=1,
                )
                last_day_of_month = first_day + relativedelta(day=31)

                months = []
                while last_day_of_month <= period_end_date + relativedelta(day=31):
                    months.append(expand_range(first_day, last_day_of_month))
                    first_day += relativedelta(months=1)
                    last_day_of_month = first_day + relativedelta(day=31)

                return [months]

            elif self.subject in RuleSubject.weekday_subjects():
                dates = []
                for a_date in expand_range(period_start_date, period_end_date):
                    if a_date.isoweekday() == self.subject.as_isoweekday():
                        dates.append(a_date)

                return [dates]

        elif self.context == RuleContext.YEAR:
            years = range(max_start_year, min_end_year + 1)

            result = []
            for year in years:
                if self.subject == RuleSubject.DAY:
                    result.append(
                        expand_range(
                            datetime.date(year=year, month=1, day=1),
                            datetime.date(year=year, month=12, day=31),
                        )
                    )
                elif self.subject == RuleSubject.WEEK:
                    year_start_date = datetime.date(year=year, month=1, day=1)
                    week_start = year_start_date - relativedelta(
                        days=year_start_date.weekday()
                    )
                    week_end = week_start + relativedelta(weekday=SU(1))

                    weeks = []
                    while week_start <= end_date:
                        weeks.append(expand_range(week_start, week_end))
                        week_start = week_start + relativedelta(weeks=1)
                        week_end = week_start + relativedelta(weekday=SU(1))

                    result.append(weeks)
                elif self.subject == RuleSubject.MONTH:
                    months = []
                    for month_number in range(1, 13):
                        first_day_of_month = datetime.date(
                            year=year, month=month_number, day=1
                        )
                        last_day_of_month = first_day_of_month + relativedelta(day=31)
                        months.append(
                            expand_range(first_day_of_month, last_day_of_month)
                        )

                    result.append(months)
                elif self.subject in RuleSubject.weekday_subjects():
                    days_in_year = expand_range(
                        datetime.date(year=year, month=1, day=1),
                        datetime.date(year=year, month=12, day=31),
                    )
                    dates = []
                    for a_date in days_in_year:
                        if a_date.isoweekday() == self.subject.as_isoweekday():
                            dates.append(a_date)

                    result.append(dates)

            return result

        elif self.context == RuleContext.MONTH:
            c = Calendar()

            first_day = datetime.date(
                year=start_date.year, month=start_date.month, day=1
            )
            last_day_of_month = first_day + relativedelta(day=31)

            result = []
            while last_day_of_month <= end_date + relativedelta(day=31):
                if self.subject == RuleSubject.DAY:
                    days_in_month = expand_range(first_day, last_day_of_month)
                    result.append(days_in_month)
                elif self.subject == RuleSubject.WEEK:
                    weeks_in_month = c.monthdatescalendar(
                        first_day.year, first_day.month
                    )
                    result.append(weeks_in_month)

                elif self.subject == RuleSubject.MONTH:
                    raise ValueError("Not applicable")

                elif self.subject in RuleSubject.weekday_subjects():
                    days_in_month = expand_range(first_day, last_day_of_month)

                    dates = []
                    for a_date in days_in_month:
                        if a_date.isoweekday() == self.subject.as_isoweekday():
                            dates.append(a_date)

                    result.append(dates)

                first_day += relativedelta(months=1)
                last_day_of_month = first_day + relativedelta(day=31)
            return result

    def apply_to_date_range(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> Set[datetime.date]:
        """Apply rule to the provided date range"""
        max_start_date = start_date
        if self.group.period.start_date:
            max_start_date = max(start_date, self.group.period.start_date)

        min_end_date = end_date
        if self.group.period.end_date:
            min_end_date = min(end_date, self.group.period.end_date)

        if max_start_date > min_end_date:
            # Period starts after the filter start date or the period ends before the
            # filter start date
            # TODO: Raise error?
            return set()

        matching_dates = set()

        # Get a set of dates that match the context and subject
        context_sets = self.get_context_sets(start_date, end_date)

        # Filter every set by start and frequency
        for context_set in context_sets:
            filtered_context_set = self._filter_context_set(context_set)
            # Flatten list of lists
            if any(isinstance(item, list) for item in filtered_context_set):
                filtered_context_set = chain(*filtered_context_set)

            matching_dates |= set(filtered_context_set)

        range_dates = set(expand_range(max_start_date, min_end_date))

        return matching_dates & range_dates
