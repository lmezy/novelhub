<script setup lang="ts">
import { useI18nStore } from "../../stores/i18n"
import { useAdminImportStore } from "../../stores/adminImport"

const i18n = useI18nStore()
const importStore = useAdminImportStore()
</script>

<template>
<section class="space-y-6">
  <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 max-w-3xl">
    <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_local_markdown') }}</h2>
    <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_local_hint') }}</p>
    <div class="flex flex-col sm:flex-row gap-2">
      <input
        v-model="importStore.localPath"
        :placeholder="i18n.t('admin_local_path_placeholder')"
        list="local-import-roots"
        class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
      />
      <datalist id="local-import-roots">
        <option v-for="root in importStore.localRoots" :key="root.path" :value="root.path">{{ root.name }}</option>
      </datalist>
      <button
        type="button"
        @click="importStore.openLocalBrowser()"
        class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5"
      >{{ i18n.t('admin_local_choose_dir') }}</button>
    </div>
    <p v-if="importStore.localError" class="text-sm text-red-600 mt-2">{{ importStore.localError }}</p>
    <div class="mt-3 flex flex-wrap items-center gap-2">
      <label class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_import_classification') }}</label>
      <select
        v-model="importStore.localClass"
        class="px-2 py-1.5 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800"
      >
        <option value="auto">{{ i18n.t('admin_classification_auto') }}</option>
        <option value="all">{{ i18n.t('admin_classification_all_ages') }}</option>
        <option value="r18">{{ i18n.t('admin_classification_r18') }}</option>
      </select>
    </div>
    <div class="mt-3 flex flex-wrap gap-2">
      <button
        @click="importStore.scanLocal"
        :disabled="importStore.localScanning"
        class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
      >{{ importStore.localScanning ? i18n.t('admin_importing_short') : i18n.t('admin_local_scan') }}</button>
      <button
        @click="importStore.importLocal"
        :disabled="importStore.localImporting"
        class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10 disabled:opacity-50"
      >{{ importStore.localImporting ? i18n.t('admin_importing_short') : i18n.t('admin_local_import') }}</button>
    </div>
    <p v-if="importStore.localScan.length" class="text-xs text-muted dark:text-gray-400 mt-3">
      {{ i18n.t('admin_local_scan_count', { n: importStore.localScan.length }) }}: {{ importStore.localScanRoot }}
    </p>
    <div v-if="importStore.localScan.length" class="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <label class="inline-flex items-center gap-1.5 cursor-pointer select-none">
        <input
          type="checkbox"
          :checked="importStore.allLocalSelected"
          @change="importStore.toggleAllLocalBooks"
          class="rounded"
        />
        <span>{{ i18n.t('admin_local_select_all') }}</span>
      </label>
      <button
        type="button"
        @click="importStore.invertLocalBooks"
        class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
      >{{ i18n.t('admin_local_select_invert') }}</button>
      <button
        type="button"
        @click="importStore.clearLocalBooks"
        class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
      >{{ i18n.t('admin_local_select_none') }}</button>
      <span class="text-muted dark:text-gray-400">{{ i18n.t('admin_local_selected_count', { n: importStore.localSelected.length }) }}</span>
    </div>
    <div v-if="importStore.localScan.length" class="mt-3 max-h-64 overflow-y-auto divide-y divide-border border border-border dark:border-gray-700 rounded">
      <label
        v-for="book in importStore.localScan"
        :key="book.path"
        class="flex items-start gap-2 px-3 py-2 cursor-pointer hover:bg-accent/5"
      >
        <input
          type="checkbox"
          :checked="importStore.localSelected.includes(book.path)"
          @change="importStore.toggleLocalBook(book.path)"
          class="mt-1 rounded"
        />
        <span class="min-w-0">
          <span class="block text-sm font-medium truncate">{{ book.title }}</span>
          <span class="block text-xs text-muted dark:text-gray-400 truncate">
            {{ book.author }} · {{ book.chapter_count }} chapters · {{ book.format }}
            <span v-if="book.is_r18" class="ml-1 px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">R18</span>
            <span v-if="book.categories?.length" class="ml-1">{{ book.categories.join(' / ') }}</span>
            <span v-if="book.tags?.length" class="ml-1">{{ book.tags.slice(0, 6).join(', ') }}</span>
          </span>
          <span class="block text-xs text-muted dark:text-gray-400 truncate">{{ book.path }}</span>
        </span>
      </label>
    </div>
    <div v-if="importStore.localScan.length" class="mt-3 flex flex-wrap gap-2">
      <button
        @click="importStore.importLocalAll"
        :disabled="importStore.localImporting || importStore.localScan.length === 0"
        class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10 disabled:opacity-50"
      >{{ i18n.t('admin_local_import_all') }}</button>
      <button
        @click="importStore.importLocalSelected"
        :disabled="importStore.localImporting || importStore.localSelected.length === 0"
        class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
      >{{ i18n.t('admin_local_import_selected') }}</button>
      <button
        @click="importStore.directLocalSelected"
        :disabled="importStore.localImporting || importStore.localSelected.length === 0"
        class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5 disabled:opacity-50"
      >{{ i18n.t('admin_local_direct') }}</button>
    </div>
    <div v-if="importStore.localResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
      <p v-if="importStore.localResult.book_id">{{ i18n.t('admin_book_id') }}: {{ importStore.localResult.book_id }}</p>
      <p v-for="item in importStore.localResult.results || []" :key="item.path">
        {{ item.path }}: {{ item.status }} {{ item.book_id || item.error || '' }}
      </p>
    </div>
    <div v-if="importStore.localDirectResult" class="mt-3 p-3 rounded bg-blue-50 dark:bg-blue-950 text-sm">
      <p v-for="book in importStore.localDirectResult.books || []" :key="book.path">
        {{ book.title }} · {{ book.chapter_count }} chapters · {{ book.path }}
        <span v-if="book.is_r18" class="ml-1 px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">R18</span>
        <span v-if="book.categories?.length" class="ml-1">{{ book.categories.join(' / ') }}</span>
        <span v-if="book.tags?.length" class="ml-1">{{ book.tags.slice(0, 6).join(', ') }}</span>
      </p>
    </div>
  </div>

  <div v-if="importStore.localBrowserOpen" class="fixed inset-0 z-[90] bg-black/50 flex items-center justify-center p-4" @click.self="importStore.localBrowserOpen = false">
    <div class="w-full max-w-lg rounded-lg border border-border bg-surface dark:bg-gray-900 p-4 shadow-xl">
      <div class="flex items-center justify-between gap-3 mb-3">
        <span class="text-sm font-medium truncate">{{ importStore.localBrowserName || importStore.localBrowserPath || i18n.t('admin_local_browse_title') }}</span>
        <button
          type="button"
          class="w-8 h-8 shrink-0 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10"
          @click="importStore.localBrowserOpen = false"
        >×</button>
      </div>
      <div class="flex flex-wrap items-center gap-2 mb-3">
        <button
          v-for="root in importStore.localRoots"
          :key="root.path"
          type="button"
          class="px-2 py-1 rounded border border-border dark:border-gray-700 text-xs hover:bg-accent/5"
          @click="importStore.browseLocalDirectory(root.path)"
        >{{ root.name }}</button>
        <button
          v-if="importStore.localBrowserParent"
          type="button"
          class="px-2 py-1 rounded border border-border dark:border-gray-700 text-xs hover:bg-accent/5"
          @click="importStore.browseLocalDirectory(importStore.localBrowserParent)"
        >{{ i18n.t('admin_local_browse_up') }}</button>
        <button
          v-if="importStore.localBrowserPath"
          type="button"
          class="px-2 py-1 rounded bg-accent text-white text-xs hover:opacity-90"
          @click="importStore.chooseLocalDirectory(importStore.localBrowserPath)"
        >{{ i18n.t('admin_local_browse_choose') }}</button>
      </div>
      <div class="max-h-64 overflow-y-auto divide-y divide-border border border-border dark:border-gray-700 rounded">
        <button
          v-for="dir in importStore.localBrowserDirs"
          :key="dir.path"
          type="button"
          class="w-full text-left px-3 py-2 hover:bg-accent/5"
          @click="importStore.browseLocalDirectory(dir.path)"
        >
          <span class="block text-sm font-medium truncate">{{ dir.name }}</span>
          <span class="block text-xs text-muted dark:text-gray-400 truncate">{{ dir.path }}</span>
        </button>
        <p v-if="!importStore.localBrowsing && importStore.localBrowserDirs.length === 0" class="px-3 py-4 text-sm text-muted dark:text-gray-400">
          {{ i18n.t('admin_local_browse_empty') }}
        </p>
        <p v-if="importStore.localBrowsing" class="px-3 py-4 text-sm text-muted dark:text-gray-400">
          {{ i18n.t('admin_local_browse_loading') }}
        </p>
      </div>
    </div>
  </div>

  <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 max-w-3xl">
    <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_manual_upload') }}</h2>
    <p class="text-xs text-muted dark:text-gray-400 mb-3" v-html="i18n.t('admin_manual_hint')"></p>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
      <input v-model="importStore.manualTitle" :placeholder="i18n.t('admin_manual_title_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      <input v-model="importStore.manualAuthor" :placeholder="i18n.t('admin_manual_author_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
      <select v-model="importStore.manualStatus" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
        <option value="ongoing">{{ i18n.t('admin_manual_status_ongoing') }}</option>
        <option value="completed">{{ i18n.t('admin_manual_status_completed') }}</option>
      </select>
      <input v-model="importStore.manualTags" :placeholder="i18n.t('admin_manual_tags_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      <select v-model="importStore.manualClass" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
        <option value="auto">{{ i18n.t('admin_classification_auto') }}</option>
        <option value="all">{{ i18n.t('admin_classification_all_ages') }}</option>
        <option value="r18">{{ i18n.t('admin_classification_r18') }}</option>
      </select>
    </div>
    <textarea
      v-model="importStore.manualDescription"
      rows="3"
      :placeholder="i18n.t('admin_manual_desc_placeholder')"
      class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y mb-3"
    ></textarea>
    <textarea
      v-model="importStore.manualChaptersText"
      rows="10"
      :placeholder="i18n.t('admin_manual_chapters_placeholder')"
      class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y font-mono mb-3"
    ></textarea>

    <div class="flex items-center gap-3 mb-3">
      <button
        type="button"
        @click="importStore.analyzeManualText()"
        :disabled="importStore.manualAnalyzing || !importStore.manualChaptersText.trim()"
        class="px-3 py-2 rounded border border-accent text-accent text-sm hover:bg-accent/10 disabled:opacity-50"
      >{{ importStore.manualAnalyzing ? i18n.t('admin_manual_analyzing') : i18n.t('admin_manual_auto_analyze') }}</button>
      <label class="inline-flex px-3 py-2 rounded border border-border dark:border-gray-700 text-sm cursor-pointer hover:bg-accent/5">
        {{ i18n.t('admin_manual_pick_file') }}
        <input type="file" accept=".txt,.md,text/plain,text/markdown" class="hidden" @change="importStore.onManualFile" />
      </label>
      <span class="text-xs text-muted dark:text-gray-400" v-html="i18n.t('admin_manual_chapter_hint')"></span>
    </div>
    <div v-if="importStore.manualAnalyzeResult" class="mb-3 p-3 rounded bg-blue-50 dark:bg-blue-950 text-sm flex flex-wrap items-center gap-2">
      <span
        :class="importStore.manualAnalyzeResult.is_r18
          ? 'px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'
          : 'px-1.5 py-0.5 rounded bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'"
      >{{ importStore.manualAnalyzeResult.is_r18 ? 'R18' : 'All-Ages' }}</span>
      <span v-if="importStore.manualAnalyzeResult.categories?.length" class="text-muted dark:text-gray-400">
        {{ importStore.manualAnalyzeResult.categories.join(' / ') }}
      </span>
      <span v-if="importStore.manualAnalyzeResult.tags?.length" class="text-muted dark:text-gray-400">
        {{ importStore.manualAnalyzeResult.tags.slice(0, 8).join(', ') }}
      </span>
    </div>

    <p v-if="importStore.manualError" class="text-sm text-red-600 mb-2">{{ importStore.manualError }}</p>
    <button
      @click="importStore.submitManualBook"
      :disabled="importStore.manualImporting"
      class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
    >{{ importStore.manualImporting ? i18n.t('admin_saving') : i18n.t('admin_save_book') }}</button>
    <div v-if="importStore.manualResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
      <p>{{ i18n.t('admin_book_id') }}: {{ importStore.manualResult.book_id }}</p>
      <p>{{ i18n.t('admin_created_chapters_label') }}: {{ importStore.manualResult.created_chapters }}</p>
      <p v-if="importStore.manualResult.is_r18" class="mt-1 px-1.5 py-0.5 inline-block rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">R18</p>
      <p v-if="importStore.manualResult.category_names?.length" class="mt-1 text-muted dark:text-gray-400">
        {{ importStore.manualResult.category_names.join(' / ') }}
      </p>
      <p v-if="importStore.manualResult.tags?.length" class="mt-1 text-muted dark:text-gray-400">
        {{ importStore.manualResult.tags.slice(0, 8).join(', ') }}
      </p>
    </div>
  </div>

</section>
</template>
