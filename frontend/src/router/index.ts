import { createRouter, createWebHistory } from "vue-router"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "home",
      component: () => import("../pages/HomePage.vue"),
    },
    {
      path: "/books",
      name: "books",
      component: () => import("../pages/BooksPage.vue"),
    },
    {
      path: "/books/:id",
      name: "book-detail",
      component: () => import("../pages/BookDetailPage.vue"),
    },
    {
      path: "/books/:bookId/chapters/:chapterId",
      name: "reader",
      component: () => import("../pages/ReaderPage.vue"),
    },
    {
      path: "/search",
      name: "search",
      component: () => import("../pages/SearchPage.vue"),
    },
    {
      path: "/login",
      name: "login",
      component: () => import("../pages/LoginPage.vue"),
    },
    {
      path: "/admin",
      name: "admin",
      component: () => import("../pages/AdminPage.vue"),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem("novelhub_token")
  if (to.name === "login" && token) {
    next({ name: "home" })
  } else if (to.name !== "login" && !token) {
    next({ name: "login" })
  } else {
    next()
  }
})

export default router
