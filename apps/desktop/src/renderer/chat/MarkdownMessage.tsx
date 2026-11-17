import { memo, useId, type ComponentPropsWithoutRef, type MouseEvent } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { safeWebUrl } from '../../shared/links';

function userClick(event: MouseEvent<HTMLAnchorElement>) {
  if (!event.isTrusted) event.preventDefault();
}
function WebLink({ href, children }: ComponentPropsWithoutRef<'a'>) {
  const safe = safeWebUrl(href);
  if (!safe) return <span>{children}</span>;
  return <a href={safe} target="_blank" rel="noopener noreferrer" onClick={userClick} onAuxClick={userClick}>{children}</a>;
}
function TableCell({ children, style }: ComponentPropsWithoutRef<'td'>) {
  const align = style?.textAlign;
  return <td className={align === 'left' || align === 'center' || align === 'right' ? `align-${align}` : undefined}>{children}</td>;
}
function TableHeading({ children, style }: ComponentPropsWithoutRef<'th'>) {
  const align = style?.textAlign;
  return <th className={align === 'left' || align === 'center' || align === 'right' ? `align-${align}` : undefined}>{children}</th>;
}
export const MarkdownMessage = memo(function MarkdownMessage({ content }: { content: string }) {
  const id = useId().replaceAll(':', '');
  const prefix = `md-${id}-`;
  return <div className="markdown-message"><Markdown skipHtml remarkPlugins={[remarkGfm]} remarkRehypeOptions={{ clobberPrefix: prefix, footnoteLabel: '注释' }} urlTransform={url => url.startsWith('#' + prefix) ? url : safeWebUrl(url) ?? ''} components={{
    a: ({ href, children, id: anchorId, 'aria-describedby': describedBy, 'aria-label': label }) => href?.startsWith('#' + prefix) ? <a href={href} id={anchorId?.startsWith(prefix) ? anchorId : undefined} aria-describedby={describedBy === 'footnote-label' ? prefix + 'footnote-label' : undefined} aria-label={label} onClick={event => {
      event.preventDefault();
      const target = document.getElementById(href.slice(1));
      if (target && event.currentTarget.closest('.markdown-message')?.contains(target)) target.scrollIntoView({ block: 'nearest' });
    }}>{children}</a> : <WebLink href={href}>{children}</WebLink>,
    h2: ({ id: headingId, children }) => <h2 id={headingId === 'footnote-label' ? prefix + headingId : undefined} className={headingId === 'footnote-label' ? 'sr-only' : undefined}>{children}</h2>,
    img: ({ alt, src }) => <span className="markdown-image-placeholder">{alt || '图片'}{safeWebUrl(src) && <> · <WebLink href={src}>查看图片</WebLink></>}</span>,
    table: ({ children }) => <div className="markdown-table"><table>{children}</table></div>,
    td: TableCell,
    th: TableHeading,
    input: ({ checked }) => <input type="checkbox" checked={!!checked} disabled readOnly aria-label={checked ? '已完成事项' : '未完成事项'}/>,
  }}>{content}</Markdown></div>;
});
