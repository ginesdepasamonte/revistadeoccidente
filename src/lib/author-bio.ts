import bio from "../../data/authors_bio.json";

export interface AuthorBio {
  qid: string;
  description: string | null;
  wikipedia_url: string | null;
  birth_year: number | null;
  death_year: number | null;
  birth_label: string | null;
  death_label: string | null;
  lifespan: string | null;
}

const entries = (bio.authors ?? {}) as unknown as Record<string, AuthorBio>;

export function authorBio(name: string): AuthorBio | undefined {
  return entries[name];
}

/** Spanish Wikipedia "Go" search: jumps to the article on an exact title match,
 *  otherwise shows search results. Used when no confirmed article URL exists. */
export function wikipediaSearchUrl(name: string): string {
  return `https://es.wikipedia.org/w/index.php?search=${encodeURIComponent(name)}&go=Ir`;
}

export interface AuthorWikipedia {
  url: string;
  confirmed: boolean;
}

export function authorWikipedia(name: string): AuthorWikipedia {
  const url = authorBio(name)?.wikipedia_url;
  return url ? { url, confirmed: true } : { url: wikipediaSearchUrl(name), confirmed: false };
}
