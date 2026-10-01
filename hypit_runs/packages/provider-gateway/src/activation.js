// Profile 激活面：config 槽 = baseUrl / apiKey(token) / concurrency / pollIntervalMs。
import {
  createRuntimeEndpointAdapterFacet, runtimeConfigCredentialRef, runtimeConfigExact,
  runtimeConfigObject, runtimeConfigPositiveInteger, runtimeConfigString,
} from "@hypit/hypit/runtime-kit";
import { createVideoProvider, providerModule } from "./provider.js";

export default {
  format: "hypit.node-package@1",
  hostFacets: [createRuntimeEndpointAdapterFacet({
    use: providerModule.name,
    activate(context) {
      const config = runtimeConfigObject(context.config, "Weeksir 网关");
      runtimeConfigExact(config, ["baseUrl", "apiKey", "concurrency", "pollIntervalMs"], "Weeksir 网关");
      const baseUrl = runtimeConfigString(config.baseUrl, "Weeksir 网关 baseUrl");
      const apiKey = runtimeConfigCredentialRef(config.apiKey, "Weeksir 网关 apiKey");
      if (!baseUrl || !apiKey || !context.pool) throw new Error("Weeksir 网关需要 baseUrl、apiKey 与 pool");
      return { endpoint: createVideoProvider({
        instance: context.instance, pool: context.pool, baseUrl, apiKey,
        concurrency: runtimeConfigPositiveInteger(config.concurrency, "concurrency") ?? 1,
        pollIntervalMs: runtimeConfigPositiveInteger(config.pollIntervalMs, "pollIntervalMs") ?? 5000,
      }) };
    },
  })],
};
