import { request } from '../request';
import { REQUEST_ID_KEY } from '~/packages/axios/src';

export function fetchUploadCheck(md5: string) {
  return request<Api.KnowledgeBase.UploadCheckResponse>({
    url: '/upload/check',
    method: 'post',
    data: { md5 }
  });
}

export function fetchUploadStatus(fileMd5: string) {
  return request<Api.KnowledgeBase.UploadStatusResponse>({
    url: '/upload/status',
    method: 'get',
    params: { file_md5: fileMd5 }
  });
}

export function fetchUploadFastUpload(md5: string) {
  return request<{ uploaded: boolean }>({
    url: '/upload/fast-upload',
    method: 'post',
    data: { md5 }
  });
}

export function fetchUploadChunk(formData: FormData, requestId: string) {
  return request<Api.KnowledgeBase.Progress>({
    url: '/upload/chunk',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data',
      [REQUEST_ID_KEY]: requestId
    },
    timeout: 10 * 60 * 1000
  });
}

export function fetchUploadMerge(fileMd5: string, fileName: string) {
  return request<{ object_url: string }>({
    url: '/upload/merge',
    method: 'post',
    data: {
      fileMd5,
      fileName
    }
  });
}
