import { create } from 'zustand';
import { translations, type Language, type Translations } from '../i18n';

export type { Language };

const STORAGE_KEY = 'novelflow-language';
const TIMEZONE_KEY = 'novelflow-timezone';

// Get stored language from localStorage
const getStoredLanguage = (): Language => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && stored in translations) {
      return stored as Language;
    }
  } catch {
    // localStorage not available
  }
  return 'vi-VN';
};

// Save language to localStorage
const storeLanguage = (language: Language) => {
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // localStorage not available
  }
};

// Get stored timezone from localStorage
const getStoredTimezone = (): string => {
  try {
    const stored = localStorage.getItem(TIMEZONE_KEY);
    if (stored) {
      return stored;
    }
  } catch {
    // localStorage not available
  }
  return 'UTC';
};

// Save timezone to localStorage
const storeTimezone = (timezone: string) => {
  try {
    localStorage.setItem(TIMEZONE_KEY, timezone);
  } catch {
    // localStorage not available
  }
};

// Translation function type
type TFunction = (key: string, params?: Record<string, string | number> & { defaultValue?: string }) => string;

interface I18nState {
  language: Language;
  timezone: string;
  setLanguage: (language: Language) => void;
  setTimezone: (timezone: string) => void;
  t: TFunction;
}

// Get nested object value
const getNestedValue = (obj: any, path: string): string | undefined => {
  const keys = path.split('.');
  let value = obj;
  for (const key of keys) {
    if (value && typeof value === 'object' && key in value) {
      value = value[key];
    } else {
      return undefined; // Translation not found, return undefined
    }
  }
  return typeof value === 'string' ? value : undefined;
};

// Create translation function
const createT = (translations: Translations) => {
  return (key: string, params?: Record<string, string | number> & { defaultValue?: string }): string => {
    // Extract defaultValue
    const defaultValue = params?.defaultValue;
    
    let value = getNestedValue(translations, key);
    
    // If translation not found, use defaultValue or original key
    if (value === undefined) {
      value = defaultValue ?? key;
    }
    
    // Replace parameters
    if (params && value) {
      Object.entries(params).forEach(([paramKey, paramValue]) => {
        if (paramKey !== 'defaultValue') {
          value = (value as string).replace(new RegExp(`{${paramKey}}`, 'g'), String(paramValue));
        }
      });
    }
    
    return value;
  };
};

export const useI18nStore = create<I18nState>((set, get) => ({
  language: getStoredLanguage(),
  timezone: getStoredTimezone(),
  t: createT(translations[getStoredLanguage()] as Translations) as TFunction,

  setLanguage: (language) => {
    storeLanguage(language);
    set({
      language,
      t: createT(translations[language] as Translations) as TFunction,
    });
  },
  
  setTimezone: (timezone) => {
    storeTimezone(timezone);
    set({ timezone });
  },
}));

// Compatible with react-i18next useTranslation hook
export const useTranslation = () => {
  const { t, language, timezone, setLanguage, setTimezone } = useI18nStore();
  return { 
    t, 
    i18n: { 
      language, 
      timezone,
      changeLanguage: setLanguage,
      changeTimezone: setTimezone,
    } 
  };
};

// Language options (Vietnamese, English, Japanese, Korean)
export const languageOptions = [
  { value: 'vi-VN', labelKey: 'uiConfig.languages.vi-VN' },
  { value: 'en-US', labelKey: 'uiConfig.languages.en-US' },
  { value: 'ja-JP', labelKey: 'uiConfig.languages.ja-JP' },
  { value: 'ko-KR', labelKey: 'uiConfig.languages.ko-KR' },
] as const;

// Timezone options
export const timezoneOptions = [
  { value: 'UTC', labelKey: 'uiConfig.timezones.UTC' },
  { value: 'Asia/Ho_Chi_Minh', labelKey: 'uiConfig.timezones.Asia/Ho_Chi_Minh' },
  { value: 'Asia/Tokyo', labelKey: 'uiConfig.timezones.Asia/Tokyo' },
  { value: 'Asia/Seoul', labelKey: 'uiConfig.timezones.Asia/Seoul' },
  { value: 'America/New_York', labelKey: 'uiConfig.timezones.America/New_York' },
  { value: 'America/Los_Angeles', labelKey: 'uiConfig.timezones.America/Los_Angeles' },
  { value: 'Europe/London', labelKey: 'uiConfig.timezones.Europe/London' },
  { value: 'Europe/Paris', labelKey: 'uiConfig.timezones.Europe/Paris' },
  { value: 'Australia/Sydney', labelKey: 'uiConfig.timezones.Australia/Sydney' },
  { value: 'UTC', labelKey: 'uiConfig.timezones.UTC' },
] as const;
