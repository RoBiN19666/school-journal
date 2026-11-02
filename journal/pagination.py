"""Общий постраничный вывод для списков журнала.

Один миксин на все списки-таблицы: размер страницы выбирается из «10, 25, 50, 100»
и запоминается в сессии пользователя, поэтому выбранное значение действует сразу
на всех страницах. Фильтры и поиск не теряются: ссылки на страницы и форма выбора
размера переносят все GET-параметры страницы (кроме «page» и «per_page»).
"""
PER_PAGE_CHOICES = (10, 25, 50, 100)
DEFAULT_PER_PAGE = 10
# Служебные параметры: они не переносятся в ссылки и скрытые поля формы.
PER_PAGE_PARAM = "per_page"
PAGE_PARAM = "page"
PER_PAGE_SESSION_KEY = "list_per_page"
# Сколько номеров страниц показывать по краям и рядом с текущей.
PAGE_RANGE_ON_EACH_SIDE = 2
PAGE_RANGE_ON_ENDS = 1


def read_per_page(request):
    """Размер страницы: из адреса (и запоминаем), из сессии или по умолчанию."""
    value = request.GET.get(PER_PAGE_PARAM, "")
    if value.isdigit() and int(value) in PER_PAGE_CHOICES:
        size = int(value)
        request.session[PER_PAGE_SESSION_KEY] = size
        return size
    stored = request.session.get(PER_PAGE_SESSION_KEY)
    if stored in PER_PAGE_CHOICES:
        return int(stored)
    return DEFAULT_PER_PAGE


def pagination_params(request):
    """GET-параметры страницы без «page» и «per_page» — их переносят ссылки и форма.

    Возвращаем список пар «имя — список значений»: у фильтров бывают
    повторяющиеся параметры, и терять их нельзя.
    """
    params = []
    for name, values in request.GET.lists():
        if name in (PAGE_PARAM, PER_PAGE_PARAM):
            continue
        params.append((name, list(values)))
    return params


class ListPaginationMixin:
    """Постраничный вывод списка с общим для всех списков размером страницы.

    Подключается перед ListView: class StudentListView(..., ListPaginationMixin, ListView).
    В шаблон достаточно добавить {% include "journal/includes/pagination.html" %}.
    """

    paginate_by = DEFAULT_PER_PAGE

    def get_paginate_by(self, queryset):
        self.per_page_size = read_per_page(self.request)
        return self.per_page_size

    def paginate_queryset(self, queryset, page_size):
        """Как в ListView, но неверный номер страницы не даёт ошибку 404.

        Номер мог остаться в адресе от прошлого раза, когда записей было больше
        (например, после смены фильтра) — показываем ближайшую существующую
        страницу, как Paginator.get_page.
        """
        paginator = self.get_paginator(
            queryset,
            page_size,
            orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty(),
        )
        number = self.request.GET.get(self.page_kwarg) or 1
        page = (
            paginator.page(paginator.num_pages)
            if number == "last"
            else paginator.get_page(number)
        )
        return paginator, page, page.object_list, page.has_other_pages()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_obj = context.get("page_obj")
        per_page = getattr(self, "per_page_size", None) or read_per_page(self.request)
        params = pagination_params(self.request)
        query = self.request.GET.copy()
        query.pop(PAGE_PARAM, None)
        query.pop(PER_PAGE_PARAM, None)
        encoded = query.urlencode()
        context.update(
            {
                "per_page": per_page,
                "per_page_choices": PER_PAGE_CHOICES,
                # Готовое начало ссылки: «?» или «?фильтры&».
                "pagination_prefix": "?" + (encoded + "&" if encoded else ""),
                "pagination_params": params,
                "total_count": page_obj.paginator.count if page_obj else 0,
            }
        )
        if page_obj and page_obj.paginator.num_pages > 1:
            context["pagination_pages"] = list(
                page_obj.paginator.get_elided_page_range(
                    page_obj.number,
                    on_each_side=PAGE_RANGE_ON_EACH_SIDE,
                    on_ends=PAGE_RANGE_ON_ENDS,
                )
            )
            context["pagination_ellipsis"] = page_obj.paginator.ELLIPSIS
        return context
