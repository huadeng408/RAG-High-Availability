<script setup lang="tsx">
import type { UploadFileInfo } from 'naive-ui';
import { NButton, NCard, NDataTable, NEllipsis, NModal, NPopconfirm, NProgress, NTag, NUpload } from 'naive-ui';
import { uploadAccept } from '@/constants/common';
import { fakePaginationRequest } from '@/service/request';
import { UploadStatus } from '@/enum';
import SvgIcon from '@/components/custom/svg-icon.vue';
import FilePreview from '@/components/custom/file-preview.vue';
import UploadDialog from './modules/upload-dialog.vue';
import SearchDialog from './modules/search-dialog.vue';

const appStore = useAppStore();

const previewVisible = ref(false);
const previewFileName = ref('');

function apiFn() {
  return fakePaginationRequest<Api.KnowledgeBase.List>({ url: '/documents/accessible' });
}

function renderIcon(fileName: string) {
  const ext = getFileExt(fileName);
  if (ext) {
    if (uploadAccept.split(',').includes(`.${ext}`)) {
      return <SvgIcon localIcon={ext} class="mx-4 text-12" />;
    }
    return <SvgIcon localIcon="dflt" class="mx-4 text-12" />;
  }
  return null;
}

function handleFilePreview(fileName: string) {
  previewFileName.value = fileName;
  previewVisible.value = true;
}

function closeFilePreview() {
  previewVisible.value = false;
  previewFileName.value = '';
}

function renderStatus(status: UploadStatus, percentage: number) {
  if (status === UploadStatus.Completed) return <NTag type="success">已完成</NTag>;
  if (status === UploadStatus.Break) return <NTag type="error">上传中断</NTag>;
  return <NProgress percentage={percentage} processing />;
}

const { columns, columnChecks, data, getData, loading } = useTable({
  apiFn,
  immediate: false,
  columns: () => [
    {
      key: 'fileName',
      title: '文件名',
      minWidth: 400,
      render: (row: any) => (
        <div class="flex items-center">
          {renderIcon(row.fileName)}
          <NEllipsis lineClamp={2} tooltip>
            <span
              class="cursor-pointer transition-colors hover:text-primary"
              onClick={() => handleFilePreview(row.fileName)}
            >
              {row.fileName}
            </span>
          </NEllipsis>
        </div>
      )
    },
    {
      key: 'totalSize',
      title: '大小',
      width: 100,
      render: (row: any) => fileSize(row.totalSize)
    },
    {
      key: 'status',
      title: '状态',
      width: 100,
      render: (row: any) => renderStatus(row.status, row.progress)
    },
    {
      key: 'orgTagName',
      title: '组织标签',
      width: 150,
      ellipsis: { tooltip: true, lineClamp: 2 }
    },
    {
      key: 'isPublic',
      title: '公开',
      width: 100,
      render: (row: any) =>
        row.public || row.isPublic ? <NTag type="success">公开</NTag> : <NTag type="warning">私有</NTag>
    },
    {
      key: 'createdAt',
      title: '上传时间',
      width: 140,
      render: (row: any) => dayjs(row.createdAt).format('YYYY-MM-DD')
    },
    {
      key: 'operate',
      title: '操作',
      width: 200,
      render: (row: any) => (
        <div class="flex gap-4">
          {renderResumeUploadButton(row)}
          <NButton type="primary" ghost size="small" onClick={() => handleFilePreview(row.fileName)}>
            预览
          </NButton>
          <NPopconfirm onPositiveClick={() => handleDelete(row.fileMd5)}>
            {{
              default: () => '确认删除当前文件吗？',
              trigger: () => (
                <NButton type="error" ghost size="small">
                  删除
                </NButton>
              )
            }}
          </NPopconfirm>
        </div>
      )
    }
  ]
});

const store = useKnowledgeBaseStore();
const { tasks } = storeToRefs(store);

onMounted(async () => {
  await getList();
  store.startUpload();
});

async function getList() {
  await getData();

  if (data.value.length === 0) {
    tasks.value = tasks.value.filter(task => task.status !== UploadStatus.Completed);
    return;
  }

  data.value.forEach((item: any) => {
    if (item.status === UploadStatus.Completed) {
      const index = tasks.value.findIndex(task => task.fileMd5 === item.fileMd5);
      if (index !== -1) {
        tasks.value[index].status = UploadStatus.Completed;
      } else {
        tasks.value.push({
          ...(item as Api.KnowledgeBase.UploadTask),
          file: null,
          chunk: null,
          requestIds: []
        });
      }
      return;
    }

    if (!tasks.value.some(task => task.fileMd5 === item.fileMd5)) {
      item.status = UploadStatus.Break;
      tasks.value.push({
        ...(item as Api.KnowledgeBase.UploadTask),
        file: null,
        chunk: null,
        requestIds: []
      });
    }
  });
}

async function handleDelete(fileMd5: string) {
  const index = tasks.value.findIndex(task => task.fileMd5 === fileMd5);
  if (index === -1) return;

  tasks.value[index].requestIds?.forEach(requestId => {
    request.cancelRequest(requestId);
  });

  if (!tasks.value[index].uploadedChunks?.length) {
    tasks.value.splice(index, 1);
    return;
  }

  const { error } = await request({ url: `/documents/${fileMd5}`, method: 'DELETE' });
  if (!error) {
    tasks.value.splice(index, 1);
    window.$message?.success('删除成功');
    await getData();
  }
}

const uploadVisible = ref(false);
function handleUpload() {
  uploadVisible.value = true;
}

const searchVisible = ref(false);
function handleSearch() {
  searchVisible.value = true;
}

function renderResumeUploadButton(row: Api.KnowledgeBase.UploadTask) {
  if (row.status !== UploadStatus.Break) return null;

  if (row.file) {
    return (
      <NButton type="primary" size="small" ghost onClick={() => resumeUpload(row)}>
        续传
      </NButton>
    );
  }

  return (
    <NUpload
      show-file-list={false}
      default-upload={false}
      accept={uploadAccept}
      onBeforeUpload={options => onBeforeUpload(options, row)}
      class="w-fit"
    >
      <NButton type="primary" size="small" ghost>
        续传
      </NButton>
    </NUpload>
  );
}

function resumeUpload(row: Api.KnowledgeBase.UploadTask) {
  row.status = UploadStatus.Pending;
  store.startUpload();
}

async function onBeforeUpload(
  options: { file: UploadFileInfo; fileList: UploadFileInfo[] },
  row: Api.KnowledgeBase.UploadTask
) {
  const file = options.file.file;
  if (!file) return false;

  const md5 = await calculateMD5(file);
  if (md5 !== row.fileMd5) {
    window.$message?.error('选择的文件与原任务不一致');
    return false;
  }

  loading.value = true;
  const { error, data: progress } = await request<Api.KnowledgeBase.UploadStatusResponse>({
    url: '/upload/status',
    params: { file_md5: row.fileMd5 }
  });

  if (!error) {
    row.file = file;
    row.status = UploadStatus.Pending;
    row.progress = progress.progress;
    row.uploadedChunks = progress.uploaded;
    row.totalChunks = progress.totalChunks;
    store.startUpload();
    loading.value = false;
    return true;
  }

  loading.value = false;
  return false;
}
</script>

<template>
  <div class="min-h-500px flex-col-stretch gap-16px overflow-hidden lt-sm:overflow-auto">
    <NCard title="文件列表" :bordered="false" size="small" class="sm:flex-1-hidden card-wrapper">
      <template #header-extra>
        <TableHeaderOperation v-model:columns="columnChecks" :loading="loading" @add="handleUpload" @refresh="getList">
          <template #prefix>
            <NButton size="small" ghost type="primary" @click="handleSearch">
              <template #icon>
                <icon-ic-round-search class="text-icon" />
              </template>
              检索知识库
            </NButton>
          </template>
        </TableHeaderOperation>
      </template>
      <NDataTable
        striped
        :columns="columns"
        :data="tasks"
        size="small"
        :flex-height="!appStore.isMobile"
        :scroll-x="962"
        :loading="loading"
        remote
        :row-key="(row: any) => row.fileMd5"
        :pagination="false"
        class="sm:h-full"
      />
    </NCard>

    <UploadDialog v-model:visible="uploadVisible" />
    <SearchDialog v-model:visible="searchVisible" />

    <NModal v-model:show="previewVisible" preset="card" title="文件预览" style="width: 80%; max-width: 1000px">
      <FilePreview :file-name="previewFileName" :visible="previewVisible" @close="closeFilePreview" />
    </NModal>
  </div>
</template>

<style scoped lang="scss">
:deep() {
  .n-progress-icon.n-progress-icon--as-text {
    white-space: nowrap;
  }
}
</style>
