// Weeksir 网关 Provider（照分发样板 examples/provider-package/packages/provider-videos 改写）。
// 协议两端都由我们定：baseUrl 指 http://localhost:8100/api/hypit（hypit 的 Profile 校验只认主机名 localhost，写 127.0.0.1 会被拒），路径 /uploads /videos /videos/{id} /rates。
import { canonicalize, defineEndpointPackage, wakeAfter } from "@hypit/hypit/endpoint-kit";
import {
  compileWireRequest, generationTypes, mappingSupportsRequest,
  sealGeneratedVideoSet, selectWireModelForRequest,
} from "@hypit/hypit/generation";

export const providerModule = { name: "@weeksir/provider-gateway", version: "1" };
export const capability = { module: { name: "@hypit/seedance", version: "1" }, name: "seedance-2-mini" };

// 字段名必须与 hypit_bridge 收的一致；视频/音频参考本网关带不过去，仍映射出去、由服务端明确拒绝（不静默丢）。
export const mapping = {
  capability, result: "video", routes: [
    { model: "seedance-2-mini" }, { model: "seedance-2.0" },
    { model: "seedance-2.5" }, { model: "omni-flash-10s" },
  ],
  fields: {
    prompt: { as: "value", field: "prompt" },
    duration: { as: "value", field: "duration" },
    resolution: { as: "value", field: "resolution" },
    aspectRatio: { as: "value", field: "ratio" },
    generateAudio: { as: "value", field: "audio" },
    webSearch: { as: "value", field: "search" },
    referenceImage: { as: "itemObject", field: "references", urlKey: "url", fieldKeys: { personReference: "person" } },
    referenceVideo: { as: "itemObject", field: "videoReferences", urlKey: "url", fieldKeys: { personReference: "person" } },
    referenceAudio: { as: "urlArray", field: "audioReferences" },
    firstFrame: { as: "url", field: "firstFrame", resourceFields: ["personReference"] },
    lastFrame: { as: "url", field: "lastFrame", resourceFields: ["personReference"] },
  },
};

const SUPPORTED_RESOLUTION = "720p";
const SUPPORTED_SECONDS = 10;      // 与 hypit_bridge.MAX_SECONDS 一致
const ACCOUNT = "hypit";           // 扣在谁的账上（= LOOP.member 口径，走网关积分）

function object(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("Expected service object");
  return value;
}
function text(value) {
  if (typeof value !== "string" || value.length === 0) throw new Error("Expected nonempty service text");
  return value;
}
function publicFailure(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return undefined;
  if (typeof value.code !== "string" || typeof value.message !== "string") return undefined;
  return { code: value.code, message: value.message.replace(/https?:\/\/\S+/giu, "[redacted-url]") };
}
function address(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) {
    throw new Error("Service URLs require HTTPS or loopback HTTP");
  }
  return url.href;
}

function serviceSupport(request) {
  const ports = request.constraints?.ports ?? {};
  const duration = ports.duration?.[0];
  if (typeof duration === "number" && duration > SUPPORTED_SECONDS) {
    return { status: "unsupported", reason: `Weeksir 网关本路由最长渲染 ${SUPPORTED_SECONDS} 秒，不是 ${duration} 秒` };
  }
  const resolution = ports.resolution?.[0];
  if (resolution !== undefined && String(resolution) !== SUPPORTED_RESOLUTION) {
    return { status: "unsupported", reason: `Weeksir 网关本路由只出 ${SUPPORTED_RESOLUTION}，不出 ${String(resolution)}` };
  }
  return mappingSupportsRequest(mapping, request.constraints)
    ? { status: "supported" }
    : { status: "unsupported", reason: "Weeksir 网关不接受其中一项输入（视频/音频参考目前带不过去）" };
}

export function createVideoProvider(options) {
  const base = address(options.baseUrl).replace(/\/$/u, "");
  const fetcher = options.fetch ?? globalThis.fetch;
  const interval = options.pollIntervalMs ?? 5000;
  const key = (credentials) => text(credentials.apiKey?.secret);

  async function json(path, secret, init = {}) {
    const response = await fetcher(`${base}${path}`, {
      ...init,
      headers: { ...init.headers, authorization: `Bearer ${secret}` },
      signal: AbortSignal.timeout(120000),   // 只覆盖 API 调用本身，不覆盖渲染分钟数
    });
    if (!response.ok) {
      let error;
      try { error = publicFailure(object(object(await response.json()).error)); } catch { /* 保留 HTTP 证据 */ }
      const requestId = response.headers.get("x-request-id");
      throw Object.assign(new Error(`Weeksir 网关 ${init.method ?? "GET"} ${path} 返回 HTTP ${response.status}`
        + (requestId === null ? "" : `; request=${requestId}`)
        + (error === undefined ? "" : `; ${error.code}: ${error.message}`)),
      error === undefined ? {} : { code: error.code });
    }
    return object(await response.json());
  }

  const endpoint = {
    async start(context) {
      const supported = serviceSupport(context.need);
      if (supported.status === "unsupported") throw new Error(supported.reason);
      const secret = key(context.credentials);
      const authored = context.need.constraints;
      const model = selectWireModelForRequest(mapping, authored);
      await context.reportProgress?.({ phase: `准备 Weeksir 网关请求: ${model}` });
      const request = await compileWireRequest(mapping, authored, async (artifact, fields) => {
        const bytes = await context.resources.get(artifact.resource);
        if (bytes === undefined) throw new Error("参考媒体不可用");
        const upload = await fetcher(`${base}/uploads`, {
          method: "POST",
          headers: {
            "content-type": artifact.mediaType, authorization: `Bearer ${secret}`,
            ...(fields?.personReference === undefined ? {} : { "x-person-reference": String(fields.personReference) }),
          },
          body: new Blob([new Uint8Array(bytes)]), signal: AbortSignal.timeout(120000),
        });
        if (!upload.ok) throw new Error(`Weeksir 网关 POST /uploads 返回 HTTP ${upload.status}`);
        return address(text(object(await upload.json()).url));
      });
      await context.reportProgress?.({ phase: `提交生成任务: ${model}` });
      const task = await json("/videos", secret, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...request, model, account: ACCOUNT }),
      });
      const id = text(task.id), handle = { id }, receipt = { id };
      await context.checkpoint?.({ handle, receipt });
      return { ...wakeAfter(handle, interval), receipt };
    },

    async poll(context) {
      const id = text(object(context.handle).id);
      const task = await json(`/videos/${encodeURIComponent(id)}`, key(context.credentials));
      const status = text(task.status);
      if (status === "queued" || status === "running") return wakeAfter({ id }, interval, Date.now(), { phase: status });
      if (status === "failed") {
        const error = publicFailure(task.error);
        return { status: "failed", receipt: { id }, failure: {
          code: error?.code ?? "WEEKSIR_GATEWAY_FAILED",
          message: `Weeksir 网关任务 ${id} 失败${error === undefined ? "" : `: ${error.message}`}`,
        } };
      }
      if (status !== "succeeded") throw new Error("Weeksir 网关返回未知任务状态");
      return { status: "ready", handle: { id, url: address(text(task.output)), credits: task.credits } };
    },

    async collect(context) {
      const url = address(text(object(context.handle).url));
      await context.reportProgress?.({ phase: "回收生成结果" });
      const response = await fetcher(url, { signal: AbortSignal.timeout(600000) });
      if (!response.ok) throw new Error(`结果下载返回 HTTP ${response.status}`);
      const mediaType = response.headers.get("content-type")?.split(";")[0]?.trim();
      if (!mediaType?.startsWith("video/")) throw new Error("Weeksir 网关返回了非视频结果");
      const artifact = await context.resources.put(new Uint8Array(await response.arrayBuffer()), mediaType);
      return { status: "completed", result: { value: {
        kind: "inline", value: canonicalize(sealGeneratedVideoSet({ videos: [artifact] })),
      } } };
    },
  };

  return defineEndpointPackage({
    module: providerModule, facet: "videos", instance: options.instance, pool: options.pool,
    credentials: { apiKey: options.apiKey },
    credentialInputs: { apiKey: { label: "Weeksir 网关 token（8100 的 MODEL_GATEWAY_TOKEN；未启用时可填任意非空串）" } },
    defaultConcurrency: options.concurrency ?? 1,
    actionLimits: { submit: { concurrency: 1 }, poll: { concurrency: 4 }, collect: { concurrency: 2 } },
    // pricing「页」必须是 https URL（endpoint-kit 硬校验），本机 loopback 不满足 ⇒ 不声明 page，
    // 只交 readPricing 的实时材料；其 source 用 http://localhost 是 endpoint-kit 明确允许的形态。
    async readPricing(context) {
      const model = selectWireModelForRequest(mapping, context.request.constraints,
        context.request.pendingInputs?.map((input) => input.input));
      const path = `/rates?model=${encodeURIComponent(model)}`;
      const rates = await json(path, key(context.credentials ? await context.credentials() : {}));
      return [{ source: `${base}${path}`, data: canonicalize(rates), summary: text(rates.description) }];
    },
    capabilities: [{ capability, returns: generationTypes.videoSet, lifecycle: "asynchronous",
                     supports: serviceSupport, endpoint }],
  });
}
