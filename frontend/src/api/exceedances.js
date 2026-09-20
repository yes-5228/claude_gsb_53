import http, { toParams } from './client.js'

export const listExceedances = (params) => http.get('/exceedances', { params: toParams(params) })
export const getExceedance = (id) => http.get(`/exceedances/${id}`)
export const annotateExceedance = (id, payload) => http.patch(`/exceedances/${id}`, payload)
export const batchAnnotate = (payload) => http.post('/exceedances/annotations', payload)
export const listExceedanceAnnotations = (id) => http.get(`/exceedances/${id}/annotations`)
export const listAnnotationBatches = (params) =>
  http.get('/exceedances/batches', { params: toParams(params) })
export const exceedanceSummary = (params) =>
  http.get('/exceedances/summary', { params: toParams(params) })
export const exceedanceOptions = () => http.get('/exceedances/options')
export const exportExceedancesUrl = (params) =>
  `/exceedances/export?${new URLSearchParams(toParams(params)).toString()}`
