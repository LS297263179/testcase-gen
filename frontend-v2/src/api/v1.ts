// ============================================================
// V1 已有端点复用（只调用，不修改 V1 运行时/路由/data.db）
//
// - /api/materials*：项目材料 CRUD（本轮 V2 UI 的「资源 · 项目材料」直接复用）
// - /api/model-config：模型配置读写（「设置」页复用）
// 注意：materials 写接口为 multipart（与 V1 前端 app.js 相同用法）。
// ============================================================

import { apiJson } from "./client";
import type { MaterialDetail, MaterialListItem } from "../types";

export const listMaterials = () =>
  apiJson<{ materials: MaterialListItem[] }>("/api/materials").then((d) => d.materials);

export const getMaterial = (id: number) => apiJson<MaterialDetail>(`/api/materials/${id}`);

export const deleteMaterial = (id: number) => apiJson<{ success: boolean }>(`/api/materials/${id}`, { method: "DELETE" });

export async function createMaterial(title: string, content: string, images: File[]): Promise<{ id: number }> {
  const fd = new FormData();
  fd.append("title", title);
  fd.append("content", content);
  images.forEach((f) => fd.append("images", f));
  return apiJson<{ success: boolean; id: number }>("/api/materials", { method: "POST", body: fd });
}

// ---- 模型配置（V1 /api/model-config，JSON） ----

export interface ModelConfig {
  [key: string]: unknown;
}

export const getModelConfig = () =>
  apiJson<{ config: ModelConfig; presets: Record<string, string> }>("/api/model-config");

export const saveModelConfig = (config: ModelConfig) =>
  apiJson<{ success: boolean; need_key?: boolean }>("/api/model-config", jsonPost(config));

function jsonPost(payload: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}
