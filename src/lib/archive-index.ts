import archive from "../../data/indice.json";

export type Issue = (typeof archive.issues)[number];
export type Contribution = Issue["contributions"][number];

export interface AuthorWork {
  kind: "article" | "note";
  issueNumber: number;
  year: number;
  month: string;
  title?: string;
  pageStart?: number | null;
  pageEnd?: number | null;
}

export interface AuthorIndexEntry {
  id: string;
  name: string;
  works: AuthorWork[];
  issueCount: number;
  articleCount: number;
  noteCount: number;
}

export interface TitleIndexEntry {
  title: string;
  authors: string[];
  issueNumber: number;
  year: number;
  month: string;
  pageStart: number | null;
  pageEnd: number | null;
}

export const issues = [...archive.issues].sort((a, b) => a.issue_number - b.issue_number);

export function searchKey(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("es")
    .replace(/-/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function slugify(value: string): string {
  return searchKey(value).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export function pageLabel(start: number | null | undefined, end: number | null | undefined): string {
  if (start == null) return "";
  return end != null && end !== start ? `pp. ${start}–${end}` : `p. ${start}`;
}

function buildAuthorIndex(): AuthorIndexEntry[] {
  interface MutableAuthor {
    name: string;
    works: AuthorWork[];
  }

  const groups = new Map<string, MutableAuthor>();
  const normalisedGroups = new Map<string, Set<string>>();

  function ensureGroup(id: string, name: string): MutableAuthor {
    let group = groups.get(id);
    if (!group) {
      group = { name, works: [] };
      groups.set(id, group);
      const key = searchKey(name);
      const ids = normalisedGroups.get(key) ?? new Set<string>();
      ids.add(id);
      normalisedGroups.set(key, ids);
    }
    return group;
  }

  for (const issue of issues) {
    for (const contribution of issue.contributions) {
      for (const author of contribution.authors) {
        const id = author.autor_id ? `dialnet:${author.autor_id}` : `name:${searchKey(author.name)}`;
        ensureGroup(id, author.name).works.push({
          kind: "article",
          issueNumber: issue.issue_number,
          year: issue.year,
          month: issue.month_name,
          title: contribution.title,
          pageStart: contribution.page_start,
          pageEnd: contribution.page_end,
        });
      }
    }
  }

  for (const issue of issues) {
    for (const noteAuthor of issue.note_authors) {
      const key = searchKey(noteAuthor.name);
      const matchingIds = normalisedGroups.get(key);
      const id = matchingIds?.size === 1 ? [...matchingIds][0] : `note:${key}`;
      const group = ensureGroup(id, noteAuthor.name);
      const duplicate = group.works.some(
        (work) => work.kind === "note" && work.issueNumber === issue.issue_number,
      );
      if (!duplicate) {
        group.works.push({
          kind: "note",
          issueNumber: issue.issue_number,
          year: issue.year,
          month: issue.month_name,
        });
      }
    }
  }

  return [...groups.entries()]
    .map(([groupId, group]) => {
      const works = group.works.sort(
        (a, b) => a.issueNumber - b.issueNumber || a.kind.localeCompare(b.kind),
      );
      return {
        id: groupId,
        name: group.name,
        works,
        issueCount: new Set(works.map((work) => work.issueNumber)).size,
        articleCount: works.filter((work) => work.kind === "article").length,
        noteCount: works.filter((work) => work.kind === "note").length,
      };
    })
    .sort((a, b) => searchKey(a.name).localeCompare(searchKey(b.name), "es"))
    .map((author, index) => ({
      ...author,
      id: `autor-${slugify(author.name) || "sin-nombre"}-${index + 1}`,
    }));
}

export const authors = buildAuthorIndex();

export const titles: TitleIndexEntry[] = issues
  .flatMap((issue) =>
    issue.contributions.map((contribution) => ({
      title: contribution.title,
      authors: contribution.authors.map((author) => author.name),
      issueNumber: issue.issue_number,
      year: issue.year,
      month: issue.month_name,
      pageStart: contribution.page_start,
      pageEnd: contribution.page_end,
    })),
  )
  .sort((a, b) => searchKey(a.title).localeCompare(searchKey(b.title), "es"));

export function indexLetter(value: string): string {
  const letter = searchKey(value).charAt(0).toUpperCase();
  return /^[A-Z]$/.test(letter) ? letter : "#";
}
