# R18 Access Control

## Source marking

When adding a source (manual source form or YueDu source import), an admin can
mark it as an R18 source. The flag is stored on `sources.is_r18`.

## Book classification

During every sync, a book is classified as R18 when:

- the source is marked R18, or
- the book title/author/description/tags contain R18 markers such as `r18`,
  `18禁`, `18+`, `成人`, `色情`, `情色`, `限制级`, `nsfw`, `h文`, `h向`.

Every synced book receives one admin-only classification tag:

- `r18` for R18 books
- `all-ages` for everything else

The classification is stored on `books.is_r18` so it can be filtered without
relying on tags.

## Visibility

- New users are created with `r18_enabled = false`.
- New users are created with `non_r18_enabled = true`, so they can see
  non-R18 books by default.
- Visibility has two independent switches:
  - `r18_enabled`: controls R18 books
  - `non_r18_enabled`: controls non-R18 books
- Both on: user sees all books. Both off: user sees no books.
- `can_manage_visibility`: admin-granted permission that shows the R18 and
  All-Ages buttons on the Home page. When off, a normal user cannot see or
  change these switches. Admins can always change any account's switches from
  Admin > User Management.
- The switches apply to every account, including `admin` and `super_admin`.
  Admins are not automatically granted full visibility; set both switches to
  on to see all books.
- Only admins can toggle another user's switches (`PUT /api/admin/users/<id>/visibility`,
  body `{"r18_enabled": true, "non_r18_enabled": true}`, 也可只传 `can_manage_visibility`）。
  被授权的用户可以用 `PUT /api/auth/me/visibility` 管理自己的开关。

- The `r18` / `all-ages` tags are only returned to admins. Normal users never
  see these classification tags, even when their R18 switch is enabled.

## Migration

`sources.is_r18` / `books.is_r18` / `users.r18_enabled` 等字段由 Alembic 迁移
（Docker 启动 backend 时自动执行）。升级后已有书籍保持 all-ages，重新同步才会更新分级；
迁移后在 Admin 点一次 `rebuild index`，让搜索索引带上 `is_r18` 字段。
