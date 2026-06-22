"use client";
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface StudioStore {
  activeBrandId: string | null;
  setActiveBrand: (id: string | null) => void;
}

export const useStudioStore = create<StudioStore>()(
  persist(
    (set) => ({
      activeBrandId: null,
      setActiveBrand: (id) => set({ activeBrandId: id }),
    }),
    { name: "studio:state" }
  )
);
