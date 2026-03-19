import { nanoid } from '~/packages/utils/src';
import { createLocalforage } from '~/packages/utils/src/storage';
import {
  fetchUploadCheck,
  fetchUploadChunk,
  fetchUploadMerge,
  fetchUploadStatus
} from '@/service/api';
import { UploadStatus } from '@/enum';
import { chunkSize } from '@/constants/common';
import { calculateBlobMD5, calculateMD5 } from '@/utils/common';

const TASK_PERSIST_KEY = 'knowledge-base-upload-tasks-v2';
const GLOBAL_CHUNK_CONCURRENCY = 8;
const PER_FILE_CHUNK_CONCURRENCY = 4;
const MAX_CHUNK_RETRY = 5;
const RETRY_BASE_DELAY_MS = 600;

type PersistedUploadTask = Omit<Api.KnowledgeBase.UploadTask, 'file' | 'chunk' | 'requestIds'>;

const uploadFileStore = createLocalforage<Record<string, File | null>>('indexedDB');

function fileStoreKey(fileMd5: string) {
  return `kb-upload-file:${fileMd5}`;
}

function sortNumberAsc(a: number, b: number) {
  return a - b;
}

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function normalizeChunkList(chunks: number[]) {
  return Array.from(new Set(chunks)).sort(sortNumberAsc);
}

export const useKnowledgeBaseStore = defineStore(SetupStoreId.KnowledgeBase, () => {
  const tasks = ref<Api.KnowledgeBase.UploadTask[]>([]);
  const activeUploads = ref<Set<string>>(new Set());

  const inflightChunksByFile = new Map<string, Set<number>>();
  const mergeInFlight = new Set<string>();

  const activeChunkCount = ref(0);
  const restoring = ref(false);

  let schedulerQueued = false;

  function getTotalChunks(task: Api.KnowledgeBase.UploadTask) {
    if (task.totalChunks && task.totalChunks > 0) return task.totalChunks;
    return Math.ceil(task.totalSize / chunkSize);
  }

  function getInflightSet(fileMd5: string) {
    let inflight = inflightChunksByFile.get(fileMd5);
    if (!inflight) {
      inflight = new Set<number>();
      inflightChunksByFile.set(fileMd5, inflight);
    }
    return inflight;
  }

  function updateActiveUploads() {
    const next = new Set<string>();
    for (const [fileMd5, inflight] of inflightChunksByFile.entries()) {
      if (inflight.size > 0) next.add(fileMd5);
    }
    activeUploads.value = next;
  }

  function readPersistedTasks(): PersistedUploadTask[] {
    const raw = window.localStorage.getItem(TASK_PERSIST_KEY);
    if (!raw) return [];

    try {
      const parsed = JSON.parse(raw) as PersistedUploadTask[];
      if (!Array.isArray(parsed)) return [];
      return parsed;
    } catch {
      return [];
    }
  }

  function toPersistedTask(task: Api.KnowledgeBase.UploadTask): PersistedUploadTask {
    const { file: _file, chunk: _chunk, requestIds: _requestIds, ...rest } = task;
    return {
      ...rest
    };
  }

  function persistTasks() {
    const toPersist = tasks.value
      .filter(task => task.status !== UploadStatus.Completed)
      .map(toPersistedTask);

    window.localStorage.setItem(TASK_PERSIST_KEY, JSON.stringify(toPersist));
  }

  async function reconcileWithServer(task: Api.KnowledgeBase.UploadTask) {
    const uploaded = new Set<number>(task.uploadedChunks || []);

    const checkResp = await fetchUploadCheck(task.fileMd5);
    if (!checkResp.error && checkResp.data.completed) {
      task.totalChunks = getTotalChunks(task);
      task.uploadedChunks = Array.from({ length: task.totalChunks }, (_, i) => i);
      task.progress = 100;
      task.status = UploadStatus.Completed;
      task.lastError = undefined;
      await uploadFileStore.removeItem(fileStoreKey(task.fileMd5));
      return;
    }
    if (!checkResp.error) {
      for (const chunk of checkResp.data.uploadedChunks || []) {
        uploaded.add(chunk);
      }
    }

    const statusResp = await fetchUploadStatus(task.fileMd5);
    if (!statusResp.error) {
      for (const chunk of statusResp.data.uploaded || []) {
        uploaded.add(chunk);
      }
      task.totalChunks = statusResp.data.totalChunks;
      task.progress = statusResp.data.progress;
    } else {
      task.totalChunks = getTotalChunks(task);
      task.progress = Number(((uploaded.size / Math.max(task.totalChunks, 1)) * 100).toFixed(2));
    }

    task.uploadedChunks = normalizeChunkList([...uploaded]);

    if (task.uploadedChunks.length >= getTotalChunks(task)) {
      task.status = UploadStatus.Pending;
    } else if (task.file) {
      task.status = UploadStatus.Pending;
    } else {
      task.status = UploadStatus.Break;
    }
  }

  async function restorePersistedTasks() {
    if (restoring.value) return;

    restoring.value = true;
    try {
      const persistedTasks = readPersistedTasks();
      if (persistedTasks.length === 0) return;

      const restored: Api.KnowledgeBase.UploadTask[] = [];
      for (const persisted of persistedTasks) {
        const file = await uploadFileStore.getItem(fileStoreKey(persisted.fileMd5));
        restored.push({
          ...persisted,
          file,
          chunk: null,
          requestIds: [],
          status: file ? UploadStatus.Pending : UploadStatus.Break
        });
      }

      tasks.value = restored;

      for (const task of tasks.value) {
        await reconcileWithServer(task);
        if (!task.file && task.status !== UploadStatus.Completed) {
          task.status = UploadStatus.Break;
          task.lastError = 'Local file cache not found. Please choose the file again to resume.';
        }
      }

      persistTasks();
      queueScheduler();
    } finally {
      restoring.value = false;
    }
  }

  async function getChunkMD5(task: Api.KnowledgeBase.UploadTask, chunkIndex: number, chunk: Blob) {
    task.chunkMd5Map ||= {};
    const cached = task.chunkMd5Map[chunkIndex];
    if (cached) return cached;

    const chunkMD5 = await calculateBlobMD5(chunk);
    task.chunkMd5Map[chunkIndex] = chunkMD5;
    return chunkMD5;
  }

  function getNextChunkIndex(task: Api.KnowledgeBase.UploadTask, inflight: Set<number>) {
    const totalChunks = getTotalChunks(task);
    const uploadedSet = new Set(task.uploadedChunks);

    for (let i = 0; i < totalChunks; i += 1) {
      if (!uploadedSet.has(i) && !inflight.has(i)) {
        return i;
      }
    }
    return -1;
  }

  async function uploadChunkWithRetry(task: Api.KnowledgeBase.UploadTask, chunkIndex: number) {
    if (!task.file) throw new Error('Local file is missing');

    let lastError: unknown = null;
    for (let attempt = 1; attempt <= MAX_CHUNK_RETRY; attempt += 1) {
      task.chunkRetryCount ||= {};
      task.chunkRetryCount[chunkIndex] = attempt;

      const chunkStart = chunkIndex * chunkSize;
      const chunkEnd = Math.min(chunkStart + chunkSize, task.totalSize);
      const chunkBlob = task.file.slice(chunkStart, chunkEnd);
      const chunkMd5 = await getChunkMD5(task, chunkIndex, chunkBlob);

      const formData = new FormData();
      formData.append('file', chunkBlob, task.file.name);
      formData.append('fileMd5', task.fileMd5);
      formData.append('fileName', task.fileName);
      formData.append('totalSize', String(task.totalSize));
      formData.append('chunkIndex', String(chunkIndex));
      formData.append('chunkMd5', chunkMd5);
      formData.append('orgTag', task.orgTag || '');
      formData.append('isPublic', String(task.isPublic ?? false));

      const requestId = nanoid();
      task.requestIds ||= [];
      task.requestIds.push(requestId);

      const { error, data } = await fetchUploadChunk(formData, requestId);
      task.requestIds = (task.requestIds || []).filter(id => id !== requestId);

      if (!error) {
        return data;
      }

      lastError = error;
      if (attempt < MAX_CHUNK_RETRY) {
        const delay = RETRY_BASE_DELAY_MS * 2 ** (attempt - 1);
        await sleep(delay);
      }
    }

    throw lastError instanceof Error ? lastError : new Error('chunk upload failed after max retries');
  }

  async function mergeTask(task: Api.KnowledgeBase.UploadTask) {
    if (mergeInFlight.has(task.fileMd5)) return;
    mergeInFlight.add(task.fileMd5);

    try {
      const { error } = await fetchUploadMerge(task.fileMd5, task.fileName);
      if (error) {
        task.status = UploadStatus.Break;
        task.lastError = 'Merge failed';
        return;
      }

      task.status = UploadStatus.Completed;
      task.progress = 100;
      task.lastError = undefined;
      await uploadFileStore.removeItem(fileStoreKey(task.fileMd5));
    } finally {
      mergeInFlight.delete(task.fileMd5);
      persistTasks();
    }
  }

  async function processChunk(task: Api.KnowledgeBase.UploadTask, chunkIndex: number) {
    try {
      const progress = await uploadChunkWithRetry(task, chunkIndex);
      task.uploadedChunks = normalizeChunkList(progress.uploaded || [...task.uploadedChunks, chunkIndex]);
      task.totalChunks = progress.totalChunks || getTotalChunks(task);
      task.progress = Number(progress.progress.toFixed(2));
      task.lastError = undefined;

      const totalChunks = getTotalChunks(task);
      if (task.uploadedChunks.length >= totalChunks && getInflightSet(task.fileMd5).size === 1) {
        await mergeTask(task);
      } else {
        task.status = UploadStatus.Uploading;
      }
    } catch (error) {
      task.status = UploadStatus.Break;
      task.lastError = error instanceof Error ? error.message : 'Chunk upload failed';
    } finally {
      const inflight = getInflightSet(task.fileMd5);
      inflight.delete(chunkIndex);
      if (inflight.size === 0) inflightChunksByFile.delete(task.fileMd5);

      activeChunkCount.value = Math.max(activeChunkCount.value - 1, 0);

      if (task.status !== UploadStatus.Completed && task.status !== UploadStatus.Break) {
        task.status = UploadStatus.Pending;
      }

      updateActiveUploads();
      persistTasks();
      queueScheduler();
    }
  }

  function queueScheduler() {
    if (schedulerQueued || restoring.value) return;
    schedulerQueued = true;

    setTimeout(() => {
      schedulerQueued = false;
      void runScheduler();
    }, 0);
  }

  async function runScheduler() {
    if (restoring.value) return;

    for (const task of tasks.value) {
      if (!task.file) continue;
      if (task.status === UploadStatus.Completed || task.status === UploadStatus.Break) continue;

      const totalChunks = getTotalChunks(task);
      if (task.uploadedChunks.length >= totalChunks) {
        if (!mergeInFlight.has(task.fileMd5) && getInflightSet(task.fileMd5).size === 0) {
          await mergeTask(task);
        }
        continue;
      }

      const inflight = getInflightSet(task.fileMd5);
      while (inflight.size < PER_FILE_CHUNK_CONCURRENCY && activeChunkCount.value < GLOBAL_CHUNK_CONCURRENCY) {
        const nextChunkIndex = getNextChunkIndex(task, inflight);
        if (nextChunkIndex < 0) break;

        task.status = UploadStatus.Uploading;
        inflight.add(nextChunkIndex);
        activeChunkCount.value += 1;
        updateActiveUploads();

        void processChunk(task, nextChunkIndex);
      }
    }
  }

  async function enqueueUpload(form: Api.KnowledgeBase.Form) {
    const file = form.fileList?.[0]?.file ?? null;
    if (!file) {
      window.$message?.error('No file selected');
      return;
    }
    if (file.size <= 0) {
      window.$message?.error('Empty file is not supported');
      return;
    }

    const fileMd5 = await calculateMD5(file);
    const existingTask = tasks.value.find(task => task.fileMd5 === fileMd5);

    if (existingTask) {
      if (existingTask.status === UploadStatus.Completed) {
        window.$message?.error('File already exists');
        return;
      }
      existingTask.file = file;
      existingTask.orgTag = form.orgTag;
      existingTask.orgTagName = form.orgTagName;
      existingTask.isPublic = form.isPublic;
      existingTask.public = form.isPublic;
      existingTask.totalSize = file.size;
      existingTask.status = UploadStatus.Pending;
      existingTask.lastError = undefined;
      await uploadFileStore.setItem(fileStoreKey(fileMd5), file);
      await reconcileWithServer(existingTask);
      persistTasks();
      queueScheduler();
      return;
    }

    const task: Api.KnowledgeBase.UploadTask = {
      file,
      chunk: null,
      chunkIndex: 0,
      fileMd5,
      fileName: file.name,
      totalSize: file.size,
      totalChunks: Math.ceil(file.size / chunkSize),
      isPublic: form.isPublic,
      public: form.isPublic,
      uploadedChunks: [],
      progress: 0,
      status: UploadStatus.Pending,
      orgTag: form.orgTag,
      orgTagName: form.orgTagName,
      requestIds: [],
      chunkMd5Map: {},
      chunkRetryCount: {},
      updatedAt: Date.now(),
      localFileKey: fileStoreKey(fileMd5)
    };

    tasks.value.push(task);
    await uploadFileStore.setItem(fileStoreKey(fileMd5), file);
    await reconcileWithServer(task);
    persistTasks();
    queueScheduler();
  }

  function startUpload() {
    if (restoring.value) return;
    void (async () => {
      for (const task of tasks.value) {
        if (!task.file) continue;
        if (task.status === UploadStatus.Completed) continue;
        await reconcileWithServer(task);
      }
      persistTasks();
      queueScheduler();
    })();
  }

  void restorePersistedTasks();

  return {
    tasks,
    activeUploads,
    enqueueUpload,
    startUpload
  };
});
