import viVN from './locales/vi-VN';
import enUS from './locales/en-US';
import jaJP from './locales/ja-JP';
import koKR from './locales/ko-KR';

export type Language = 'vi-VN' | 'en-US' | 'ja-JP' | 'ko-KR';

export const translations = {
  'vi-VN': viVN,
  'en-US': enUS,
  'ja-JP': jaJP,
  'ko-KR': koKR,
};

export type Translations = typeof viVN;
