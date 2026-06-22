import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import AsyncStorage from "@react-native-async-storage/async-storage";

interface Store {
  activeBrandId: string | null;
  setActiveBrand: (id: string | null) => void;
}

export const useStore = create<Store>()(
  persist(
    (set) => ({
      activeBrandId: null,
      setActiveBrand: (id) => set({ activeBrandId: id }),
    }),
    { name: "studio:mobile", storage: createJSONStorage(() => AsyncStorage) }
  )
);
